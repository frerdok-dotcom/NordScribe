"""
Слой аудио-обработки NordScribe.

Две независимые обязанности:

* :class:`AudioRecorder` — запись с микрофона через ``sounddevice`` с потоковой
  записью на диск и обрезкой тишины по краям;
* :class:`WhisperWorker` — владение моделью Whisper, проверка целостности
  её файла и транскрибация в отдельном потоке.

Модуль ничего не знает о виджетах: он вызывает переданные callback-и из
рабочего потока, а переключать их в главный поток обязан UI-слой
(через ``.after()``). Единственное исключение — ``on_tick`` у рекордера,
который вызывается ровно раз в секунду.
"""

from __future__ import annotations

import logging
import math
import os
import queue
import tempfile
import threading
import wave
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

import numpy as np
import sounddevice as sd
import whisper

import download_manager as dm

__all__ = [
    "RecorderError",
    "TranscriptionError",
    "AudioRecorder",
    "WhisperWorker",
    "cleanup_temp_file",
    "trim_silence_bounds",
]

logger = logging.getLogger(__name__)

#: Сообщение о фазе операции (вызывается из рабочего потока).
StatusCallback = Callable[[str], None]
#: Секунды с начала записи (вызывается из потока-писателя).
TickCallback = Callable[[float], None]
#: Готовый текст и путь к обработанному файлу.
DoneCallback = Callable[[str, Path], None]
#: Любая ошибка обработки.
ErrorCallback = Callable[[BaseException], None]

#: Сколько секунд тишины в начале записи разрешено держать в памяти, пока
#: не появится первый громкий фрагмент. Ограничивает память на «пустых»
#: записях, где звук так и не появился.
_LEAD_IN_CAP_SECONDS = 5.0


class RecorderError(Exception):
    """Не удалось начать или завершить запись с микрофона."""


class TranscriptionError(Exception):
    """Whisper не смог обработать аудиофайл."""


def cleanup_temp_file(path: Optional[Path]) -> bool:
    """
    Удаляет временный файл, созданный рекордером.

    Returns:
        True, если файл существовал и был удалён.
    """
    if path is None:
        return False
    try:
        Path(path).unlink()
        return True
    except OSError:
        return False


def _peak_to_db(peak: int) -> float:
    """Переводит пиковую амплитуду int16 в дБFS. Тишина даёт -inf."""
    if peak <= 0:
        return float("-inf")
    return 20.0 * math.log10(min(peak, 32768) / 32768.0)


def trim_silence_bounds(
    samples: np.ndarray,
    threshold_db: float = -45.0,
    margin_seconds: float = 0.3,
    sample_rate: int = 16000,
) -> tuple[int, int]:
    """
    Находит границы звучащей части сигнала, отбрасывая тишину по краям.

    Функция чистая и не трогает внутренние паузы: разрывать речь на части
    нельзя — Whisper теряет контекст и качество распознавания падает.

    Args:
        samples: одномерный массив int16.
        threshold_db: порог тишины в дБFS.
        margin_seconds: сколько секунд тишины оставить перед и после речи.
        sample_rate: частота дискретизации.

    Returns:
        Пара (начало, конец) в сэмплах. Для полной тишины возвращает (0, 0).
    """
    if samples.size == 0:
        return (0, 0)

    margin = max(int(margin_seconds * sample_rate), 0)
    window = max(sample_rate // 100, 1)  # окна по 10 мс
    usable = (samples.size // window) * window
    if usable == 0:
        return (0, samples.size)

    blocks = samples[:usable].astype(np.int32).reshape(-1, window)
    peaks = np.abs(blocks).max(axis=1)
    # Нормируем пик окна к полной шкале int16.
    with np.errstate(divide="ignore"):
        loud = peaks >= (32768.0 * (10.0 ** (threshold_db / 20.0)))

    indices = np.flatnonzero(loud)
    if indices.size == 0:
        return (0, 0)

    start = max(int(indices[0]) * window - margin, 0)
    end = min((int(indices[-1]) + 1) * window + margin, samples.size)
    return (start, end)


class AudioRecorder:
    """
    Запись с микрофона.

    Кадры не накапливаются в памяти: callback PortAudio кладёт их в очередь,
    а отдельный поток-писатель сразу пишет их в WAV. Пиковая память не
    зависит от длительности записи (раньше двухчасовая запись требовала
    около 440 МБ).

    Обрезка тишины выполняется на лету: ведущая тишина не пишется вовсе,
    хвостовая отбрасывается при закрытии файла. Внутренние паузы не трогаются.

    Args:
        sample_rate: частота дискретизации; 16 кГц — то, что ждёт Whisper.
        on_tick: вызывается раз в секунду с длительностью записи.
        stream_factory: фабрика входного потока; подменяется в тестах,
            чтобы не требовать реального микрофона.
        trim_silence: обрезать ли тишину по краям записи.
        silence_threshold_db: порог тишины в дБFS.
        silence_margin_seconds: сколько тишины оставить вокруг речи.
    """

    _QUEUE_TIMEOUT = 0.1

    def __init__(
        self,
        sample_rate: int = 16000,
        on_tick: Optional[TickCallback] = None,
        stream_factory: Optional[Callable[..., Any]] = None,
        trim_silence: bool = True,
        silence_threshold_db: float = -45.0,
        silence_margin_seconds: float = 0.3,
    ) -> None:
        self.sample_rate = sample_rate
        self._on_tick = on_tick
        self._stream_factory = stream_factory or sd.InputStream
        self._trim = trim_silence
        self._threshold_db = silence_threshold_db
        self._margin_frames = max(int(silence_margin_seconds * sample_rate), 0)
        self._lead_in_cap_frames = int(_LEAD_IN_CAP_SECONDS * sample_rate)

        self._queue: queue.Queue[tuple[bytes, int]] = queue.Queue()
        self._stream: Any = None
        self._writer: Optional[threading.Thread] = None
        self._wave: Optional[wave.Wave_write] = None
        self._temp_path: Optional[Path] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._write_error: Optional[BaseException] = None

        self._pending: deque[tuple[bytes, int]] = deque()
        self._pending_frames = 0
        self._loud_seen = False
        self._frames_total = 0
        self._frames_written = 0
        self._last_reported_second = -1

    @property
    def is_recording(self) -> bool:
        """Идёт ли запись прямо сейчас."""
        with self._lock:
            return self._stream is not None

    @property
    def duration(self) -> float:
        """Длительность принятого аудио в секундах."""
        if self.sample_rate <= 0:
            return 0.0
        return self._frames_total / self.sample_rate

    @property
    def frames_written(self) -> int:
        """Сколько кадров реально попало в файл после обрезки тишины."""
        return self._frames_written

    def start(self) -> None:
        """
        Открывает WAV, запускает поток-писатель и входной поток.

        Raises:
            RecorderError: не удалось создать файл или открыть устройство ввода.
        """
        with self._lock:
            if self._stream is not None:
                return
            self._reset_state()
            self._open_sink()
            self._writer = threading.Thread(
                target=self.record_audio, name="recorder-writer", daemon=True
            )
            self._writer.start()
            try:
                stream = self._stream_factory(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="int16",
                    callback=self.audio_callback,
                )
                stream.start()
            except Exception as exc:
                self._stop_event.set()
                if self._writer is not None:
                    self._writer.join(timeout=1.0)
                    self._writer = None
                self._close_sink()
                cleanup_temp_file(self._temp_path)
                self._temp_path = None
                raise RecorderError(str(exc)) from exc
            self._stream = stream

    def _reset_state(self) -> None:
        """Готовит поля к новой записи."""
        self._queue = queue.Queue()
        self._pending.clear()
        self._pending_frames = 0
        self._loud_seen = False
        self._frames_total = 0
        self._frames_written = 0
        self._last_reported_second = -1
        self._write_error = None
        self._stop_event.clear()

    def _open_sink(self) -> None:
        """Создаёт временный WAV и открывает его на запись."""
        handle, name = tempfile.mkstemp(suffix=".wav", prefix="nordscribe_")
        try:
            os.close(handle)
            self._temp_path = Path(name)
            self._wave = wave.open(str(self._temp_path), "wb")
            self._wave.setnchannels(1)
            self._wave.setsampwidth(2)  # int16
            self._wave.setframerate(self.sample_rate)
        except Exception as exc:
            cleanup_temp_file(Path(name))
            raise RecorderError(f"не удалось создать файл записи: {exc}") from exc

    def _close_sink(self) -> None:
        """Закрывает WAV, дописывая корректный заголовок."""
        if self._wave is not None:
            try:
                self._wave.close()
            except Exception as exc:  # noqa: BLE001 — закрытие не должно падать
                logger.warning("ошибка закрытия WAV: %s", exc)
            self._wave = None

    def audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        """
        Callback PortAudio: считает пик и кладёт блок в очередь.

        Здесь нет ни записи на диск, ни блокировок, способных задержать
        аудиопоток: копирование в байты и взятие максимума занимают
        микросекунды на блок.
        """
        with self._lock:
            if self._stream is None:
                return
        try:
            mono = indata[:, 0] if indata.ndim > 1 else indata
            peak = int(np.abs(mono).max()) if mono.size else 0
            payload = mono.astype(np.int16, copy=True).tobytes()
        except Exception:  # noqa: BLE001 — не роняем аудиопоток из-за одного блока
            logger.exception("не удалось обработать блок аудио, блок пропущен")
            return
        self._queue.put((payload, peak))

    def record_audio(self) -> None:
        """
        Поток-писатель: разбирает очередь, пишет WAV и считает длительность.

        Вызывается раз в секунду ``on_tick``. Раньше это делал цикл в потоке
        записи, причём условие ``int(duration) % 1 == 0`` было всегда истинным
        и статус дёргался 10 раз в секунду.
        """
        while True:
            try:
                block, peak = self._queue.get(timeout=self._QUEUE_TIMEOUT)
            except queue.Empty:
                if self._stop_event.is_set() and self._queue.empty():
                    break
                self._report_tick()
                continue

            try:
                self._write_block(block, peak)
            except Exception as exc:  # noqa: BLE001 — диск может отказать
                self._write_error = exc
                logger.exception("ошибка записи аудио на диск")
                break
            self._report_tick()

        self._flush_tail()
        self._close_sink()

    def _report_tick(self) -> None:
        """
        Сообщает длительность не чаще раза в секунду.

        Обработчик обёрнут в try: он вызывает пользовательский код (в
        приложении — обновление виджета через ``after()``), и любое исключение
        там убивало поток-писатель. Запись при этом молча терялась целиком:
        файл оставался пустым, а пользователь видел только «Аудио не записано».
        """
        if self._on_tick is None:
            return
        second = int(self.duration)
        if second == self._last_reported_second:
            return
        self._last_reported_second = second
        try:
            self._on_tick(self.duration)
        except Exception:  # noqa: BLE001 — запись важнее статуса
            logger.exception("сбой в обработчике on_tick, запись продолжается")

    def _write_block(self, block: bytes, peak: int) -> None:
        """Пишет блок, отбрасывая тишину по краям, если обрезка включена."""
        wave_out = self._wave
        if wave_out is None:
            return

        frames = len(block) // 2
        self._frames_total += frames

        if not self._trim:
            wave_out.writeframes(block)
            self._frames_written += frames
            return

        if _peak_to_db(peak) >= self._threshold_db:
            # Перед речью отдаём накопленный запас тишины и пишем блок.
            self._dump_pending(limit=self._margin_frames)
            wave_out.writeframes(block)
            self._frames_written += frames
            self._loud_seen = True
            return

        self._pending.append((block, frames))
        self._pending_frames += frames
        cap = self._margin_frames if self._loud_seen else self._lead_in_cap_frames
        while self._pending_frames > cap and len(self._pending) > 1:
            _, dropped = self._pending.popleft()
            self._pending_frames -= dropped

    def _dump_pending(self, limit: Optional[int] = None) -> None:
        """Записывает накопленные блоки тишины, не более ``limit`` кадров."""
        wave_out = self._wave
        if wave_out is None:
            return
        written = 0
        while self._pending:
            block, frames = self._pending.popleft()
            self._pending_frames -= frames
            if limit is not None and written >= limit:
                continue
            wave_out.writeframes(block)
            self._frames_written += frames
            written += frames

    def _flush_tail(self) -> None:
        """Дописывает хвост записи при закрытии."""
        if not self._trim or not self._loud_seen:
            # Обрезка выключена или речи так и не было — не теряем данные.
            self._dump_pending()
            return
        self._dump_pending(limit=self._margin_frames)
        self._pending.clear()
        self._pending_frames = 0

    def stop(self) -> Optional[Path]:
        """
        Останавливает запись и возвращает путь к готовому WAV.

        Returns:
            Путь к файлу или ``None``, если не записано ни одного кадра.

        Raises:
            RecorderError: не удалось записать данные на диск.
        """
        with self._lock:
            stream, self._stream = self._stream, None

        self._stop_event.set()

        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:  # noqa: BLE001 — закрытие не критично
                logger.debug("ошибка закрытия входного потока: %s", exc)

        if self._writer is not None:
            self._writer.join(timeout=2.0)
            self._writer = None

        self._close_sink()

        if self._write_error is not None:
            error, self._write_error = self._write_error, None
            cleanup_temp_file(self._temp_path)
            self._temp_path = None
            raise RecorderError(f"не удалось сохранить запись: {error}")

        if self._frames_written == 0:
            cleanup_temp_file(self._temp_path)
            self._temp_path = None
            return None
        return self._temp_path


class WhisperWorker:
    """
    Владеет моделью Whisper и транскрибирует файлы в отдельном потоке.

    Тяжёлые задачи выполняются в пуле из одного рабочего потока: это
    сериализует обработку (две транскрибации одновременно бессмысленны и
    борются за CPU) и не плодит потоки на каждое нажатие кнопки.

    Модель загружается лениво — при первой задаче, а не в конструкторе,
    поэтому запуск приложения и нажатие кнопок никогда не блокируются
    загрузкой весов размером до 2.9 ГБ.

    Перед загрузкой файл модели проверяется по SHA-256 из её URL. Битый
    или обрезанный файл удаляется и скачивается заново — вместо невнятной
    ошибки torch пользователь получает самолечение.

    Args:
        model_name_provider: функция, возвращающая выбранное в UI имя модели.
            Вызывается каждый раз перед загрузкой, поэтому смена модели
            в меню подхватывается.
        language: язык распознавания, по умолчанию русский.
        verify_cache: проверять ли целостность файла модели перед загрузкой.
    """

    def __init__(
        self,
        model_name_provider: Callable[[], str],
        language: str = "ru",
        verify_cache: bool = True,
    ) -> None:
        self._model_name_provider = model_name_provider
        self._language = language
        self._verify_cache = verify_cache
        self._model: Any = None
        self._loaded_name: Optional[str] = None
        self._model_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="whisper"
        )

    @property
    def loaded_model_name(self) -> Optional[str]:
        """Имя загруженной модели или ``None``, если модель ещё не загружена."""
        return self._loaded_name

    def ensure_model(self, on_status: Optional[StatusCallback] = None) -> str:
        """
        Загружает модель, если она не загружена или выбор в UI изменился.

        Блокирующая операция: вызывать только из рабочего потока.
        Повторные вызовы для уже загруженной модели ничего не делают.

        Raises:
            TranscriptionError: модель не удалось загрузить.
        """
        with self._model_lock:
            name = self._model_name_provider()
            if self._model is not None and self._loaded_name == name:
                return name
            if self._verify_cache:
                self._repair_cache(name, on_status)
            if on_status is not None:
                on_status(f"Загрузка модели Whisper ({name})...")
            try:
                self._model = whisper.load_model(name)
            except Exception as exc:
                raise TranscriptionError(
                    f"не удалось загрузить модель '{name}': {exc}"
                ) from exc
            self._loaded_name = name
            return name

    def _repair_cache(
        self, name: str, on_status: Optional[StatusCallback] = None
    ) -> None:
        """Удаляет повреждённый файл модели, чтобы библиотека скачала его заново."""
        try:
            valid = dm.cached_model_is_valid(name)
        except OSError as exc:
            logger.warning("не удалось проверить файл модели '%s': %s", name, exc)
            return
        if valid is not False:
            return
        logger.warning("файл модели '%s' повреждён — удаляю и перекачиваю", name)
        if on_status is not None:
            on_status(f"Файл модели '{name}' повреждён, скачиваю заново...")
        removed = dm.remove_model_file(name)
        logger.info("удалены повреждённые файлы: %s", removed)

    def transcribe(
        self,
        audio_path: Path,
        on_status: Optional[StatusCallback] = None,
    ) -> str:
        """
        Распознаёт речь в файле. Блокирующая операция для рабочего потока.

        Raises:
            TranscriptionError: модель не загрузилась или файл не читается.
        """
        try:
            self.ensure_model(on_status)
            if on_status is not None:
                on_status("Начинаю распознавание...")
            result = self._model.transcribe(
                str(audio_path),
                language=self._language,
                task="transcribe",
                verbose=False,
            )
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(str(exc)) from exc
        return str(result.get("text", "")).strip()

    def submit(
        self,
        audio_path: Path,
        on_done: DoneCallback,
        on_error: ErrorCallback,
        on_status: Optional[StatusCallback] = None,
    ) -> Future:
        """
        Ставит транскрибацию в очередь рабочего потока.

        Callback-и вызываются из рабочего потока — UI обязан переключить
        их в главный поток через ``.after()``.

        Returns:
            Future, позволяющий дождаться завершения (нужен тестам).
        """

        def job() -> None:
            try:
                text = self.transcribe(audio_path, on_status)
            except BaseException as exc:  # noqa: BLE001 — сообщаем о любой ошибке
                on_error(exc)
            else:
                on_done(text, Path(audio_path))

        return self._executor.submit(job)

    def shutdown(self, wait: bool = False) -> None:
        """Останавливает пул. Незапущенные задачи отменяются."""
        self._executor.shutdown(wait=wait, cancel_futures=True)
