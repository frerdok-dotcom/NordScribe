"""
NordScribe — локальная транскрибация аудио через OpenAI Whisper.

Точка входа и главное окно. Модуль намеренно тонкий: он только связывает
виджеты с бэкендом.

* сетевая логика и работа с файлами моделей — :mod:`download_manager`;
* запись звука и распознавание — :mod:`whisper_worker`;
* чистые подписи моделей — :mod:`model_labels`;
* окно диспетчера и покраска меню — :mod:`gui_components`;
* вкладки интерфейса — :mod:`tab_transcription` и :mod:`tab_history`;
* палитра и шрифты — :mod:`theme`;
* настройки и журнал — :mod:`settings`;
* проверки окружения — :mod:`preflight`.

Запуск::

    python main.py
"""

from __future__ import annotations

import logging
import multiprocessing
import re
import sys
from pathlib import Path
from tkinter import filedialog as fd

import customtkinter as ctk

import download_manager as dm
import gui_components as gui
import history
import model_labels as labels
import preflight
import settings as app_settings
import whisper_worker as ww
from tab_history import HistoryTab
from tab_transcription import IDLE_STATUS, TranscriptionTab
from theme import (
    COLOR_ACCENT,
    COLOR_GREY,
    COLOR_GREY_HOVER,
    COLOR_RED,
    STATUS_GRAY,
    STATUS_GREEN,
    STATUS_ORANGE,
    ui_font,
)

__all__ = ["NordScribeApp", "main"]

logger = logging.getLogger(__name__)

#: Допустимая строка геометрии окна вида 1100x700 или 1100x700+100+50.
_GEOMETRY_RE = re.compile(r"^\d+x\d+([+-]\d+[+-]\d+)?$")


class NordScribeApp(ctk.CTk):
    """
    Главное окно приложения.

    Тяжёлые операции (загрузка весов Whisper, распознавание) выполняются
    в :class:`whisper_worker.WhisperWorker`, поэтому нажатие любой кнопки
    не блокирует интерфейс. Все обновления виджетов из рабочих потоков
    проходят через ``after(0, ...)``.
    """

    SAMPLE_RATE = 16000

    def __init__(self, settings: app_settings.Settings | None = None) -> None:
        super().__init__()

        self.settings = settings or app_settings.Settings()
        self.title("NordScribe - Локальная Транскрибация")
        self._apply_saved_geometry()

        # --- состояние моделей ---
        self.downloaded_whisper_models: set[str] = dm.get_downloaded_whisper_models()
        selected = (
            self.settings.model
            if self.settings.model in dm.WHISPER_MODELS
            else dm.WHISPER_MODELS[0]
        )
        self.whisper_display_options: list[str] = labels.build_model_labels(
            dm.WHISPER_MODELS, self.downloaded_whisper_models
        )
        # Две РАЗНЫЕ переменные, и это принципиально:
        #   whisper_var         — чистое имя модели ("large"), идёт в load_model;
        #   whisper_display_var — подпись для виджета ("🟢 large").
        # CTkOptionMenu показывает текст, привязанный к своей variable, поэтому
        # если хранить в ней чистое имя, маркер в свёрнутом виде не виден.
        self.whisper_var = ctk.StringVar(value=selected)
        self.whisper_display_var = ctk.StringVar(
            value=labels.label_for_model(self.whisper_display_options, selected)
        )

        # --- фоновые задачи ---
        self._download_active = False
        self._transcription_active = False
        self._pending_temp: Path | None = None
        self.model_manager: gui.ModelManagerWindow | None = None

        self.recorder = ww.AudioRecorder(
            sample_rate=self.SAMPLE_RATE,
            on_tick=self._on_recording_tick,
            trim_silence=self.settings.trim_silence,
        )
        self.worker = ww.WhisperWorker(
            model_name_provider=self._selected_model_name,
            language=self.settings.language,
        )

        # Семейство шрифта с цветными эмодзи: иначе маркеры и значки
        # на кнопках рисуются монохромными квадратами.
        self._emoji_family = gui.pick_emoji_font_family()

        # История транскрибаций: своя база в каталоге данных приложения
        # (~/.nordscribe/history.db), а не индекс CodeGraph из .codegraph/.
        self.history = history.TranscriptionHistory()
        self._history_available = self.history.init_schema()

        self._build_layout()
        gui.apply_menu_item_colors(
            self.whisper_menu, dm.WHISPER_MODELS, self.downloaded_whisper_models
        )
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._run_preflight()
        self._load_history()

    # ------------------------------------------------------------------
    # запуск и завершение
    # ------------------------------------------------------------------

    def _apply_saved_geometry(self) -> None:
        """Восстанавливает размер окна, если сохранённое значение корректно."""
        geometry = (self.settings.window_geometry or "").strip()
        if _GEOMETRY_RE.match(geometry):
            self.geometry(geometry)
        else:
            logger.debug("геометрия %r не распознана, беру размер по умолчанию", geometry)
            self.geometry("1100x700")

    def _run_preflight(self) -> None:
        """Проверяет окружение и сообщает о проблемах в строке статуса."""
        try:
            results = preflight.run_checks()
        except Exception:
            logger.exception("проверки окружения не выполнены")
            return
        summary = preflight.summarize(results)
        if summary:
            self.transcription.set_status(summary, STATUS_ORANGE)
        logger.info(
            "окружение: %s",
            "; ".join(f"{r.name}={'ok' if r.ok else 'нет'}" for r in results),
        )

    def destroy(self) -> None:
        """Сохраняет настройки, останавливает запись и глушит пул задач."""
        try:
            self._shutdown()
        except Exception:
            logger.exception("ошибка при завершении работы")
        finally:
            super().destroy()

    def _shutdown(self) -> None:
        """
        Освобождает ресурсы перед выходом.

        Раньше окно закрывалось, оставив открытым поток PortAudio и болтающийся
        временный WAV: запись не останавливалась, а файл никто не удалял.
        """
        self.history_tab.cancel_clear()
        self.history_tab.disable_mousewheel()
        if self.recorder.is_recording:
            try:
                ww.cleanup_temp_file(self.recorder.stop())
            except ww.RecorderError as exc:
                logger.warning("не удалось корректно остановить запись: %s", exc)
        if ww.cleanup_temp_file(self._pending_temp):
            logger.info("удалён незавершённый временный файл")
        self._pending_temp = None

        self.settings.update(
            model=self._selected_model_name(),
            window_geometry=self.geometry(),
        )
        self.settings.save()

        self.worker.shutdown(wait=False)
        logger.info("приложение остановлено")

    # ------------------------------------------------------------------
    # построение интерфейса
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        """Создаёт все виджеты главного окна."""
        # Одна колонка во всю ширину: панель настроек и область вкладок.
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        self._build_settings_row()
        self._build_tabbed_area()

    def _build_settings_row(self) -> None:
        """Верхняя панель: выбор модели и кнопка диспетчера."""
        self.settings_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.settings_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 5))
        self.settings_frame.grid_columnconfigure(0, weight=0)
        self.settings_frame.grid_columnconfigure(1, weight=0)
        self.settings_frame.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(
            self.settings_frame,
            text="Модель Whisper:",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.whisper_menu = ctk.CTkOptionMenu(
            self.settings_frame,
            variable=self.whisper_display_var,
            values=self.whisper_display_options,
            command=self._on_whisper_model_change,
            width=140,
            height=30,
            font=self._ui_font(size=13),
            dropdown_font=self._ui_font(size=13),
        )
        self.whisper_menu.grid(row=0, column=1, sticky="w")

        self.btn_manage_models = ctk.CTkButton(
            self.settings_frame,
            text="⚙ Управление моделями",
            command=self.open_model_manager,
            width=190,
            height=30,
            fg_color=COLOR_GREY,
            hover_color=COLOR_GREY_HOVER,
        )
        self.btn_manage_models.grid(row=0, column=2, sticky="e")

    def _ui_font(self, size: int = 13, bold: bool = False) -> ctk.CTkFont:
        """Шрифт интерфейса (см. :func:`theme.ui_font`)."""
        return ui_font(self._emoji_family, size=size, bold=bold)

    def _build_tabbed_area(self) -> None:
        """
        Делит основную область на вкладки — всё остаётся в одном окне.

        CTkTabview растягивает активную вкладку (внутри он ставит её с
        sticky="nsew" в строку с weight=1), поэтому поле транскрибации
        занимает всю высоту, как и до появления истории. Виджеты самих
        вкладок живут в :mod:`tab_transcription` и :mod:`tab_history`.
        """
        tabview = ctk.CTkTabview(self)
        tabview.grid(row=1, column=0, sticky="nsew", padx=15, pady=(0, 15))
        self.transcription = TranscriptionTab(
            tabview.add("Транскрибация"),
            emoji_family=self._emoji_family,
            on_load_audio=self.start_transcription_thread,
            on_toggle_recording=self.toggle_recording,
        )
        self.history_tab = HistoryTab(
            tabview.add("История"),
            emoji_family=self._emoji_family,
            on_cleared=self._clear_history,
        )
        if not self._history_available:
            self.history_tab.set_enabled(False)

    # ------------------------------------------------------------------
    # история транскрибаций
    # ------------------------------------------------------------------

    def _load_history(self) -> None:
        """Заполняет вкладку истории записями из базы при старте приложения."""
        if not self._history_available:
            if self.transcription.lbl_status.cget("text") == IDLE_STATUS:
                self.transcription.set_status(
                    "История недоступна: не удалось открыть базу данных",
                    STATUS_ORANGE,
                )
            self.history_tab.show_placeholder("История недоступна")
            return

        entries = self.history.all()
        self.history_tab.load(entries)
        logger.info("история: загружено записей — %d", len(entries))

    def _save_to_history(self, text: str) -> None:
        """Пишет результат в SQLite и сразу показывает карточку в истории."""
        if not text.strip():
            return
        if not self._history_available and not self.history.init_schema():
            return
        entry = self.history.add(text)
        if entry is None:
            return
        self._history_available = True
        self.history_tab.set_enabled(True)
        self.history_tab.prepend(entry)
        logger.info("в историю добавлена запись id=%d", entry.id)

    def _clear_history(self) -> int:
        """Удаляет записи из базы и все карточки из вкладки."""
        removed = self.history.clear()
        self.history_tab.clear_cards()
        self.history_tab.show_placeholder("История пуста")
        self.transcription.set_status(f"История очищена (удалено записей: {removed})", STATUS_GRAY)
        logger.info("история очищена, удалено записей: %d", removed)
        return removed

    # ------------------------------------------------------------------
    # модели
    # ------------------------------------------------------------------

    def _selected_model_name(self) -> str:
        """Чистое имя выбранной модели — то, что уходит в whisper.load_model()."""
        return labels.clean_model_label(self.whisper_var.get())

    def refresh_whisper_models(self) -> set[str]:
        """
        Перечитывает кэш и обновляет подписи и цвета в выпадающем списке.

        Возвращает новое множество доступных моделей (его же запрашивает
        диспетчер, чтобы не держать собственное состояние).
        """
        self.downloaded_whisper_models = dm.get_downloaded_whisper_models()

        selected = self._selected_model_name()
        if selected not in dm.WHISPER_MODELS:
            selected = dm.WHISPER_MODELS[0]

        self.whisper_display_options = labels.build_model_labels(
            dm.WHISPER_MODELS, self.downloaded_whisper_models
        )
        self.whisper_menu.configure(values=self.whisper_display_options)
        # configure(values=...) пересобирает пункты — цвета накладываем заново.
        gui.apply_menu_item_colors(
            self.whisper_menu, dm.WHISPER_MODELS, self.downloaded_whisper_models
        )
        self.whisper_var.set(selected)
        self.whisper_display_var.set(
            labels.label_for_model(self.whisper_display_options, selected)
        )
        return self.downloaded_whisper_models

    def _on_whisper_model_change(self, choice: str) -> None:
        """Сохраняет чистое имя модели и показывает каноническую подпись."""
        clean_name = labels.clean_model_label(choice)
        self.whisper_var.set(clean_name)
        self.whisper_display_var.set(
            labels.label_for_model(self.whisper_display_options, clean_name)
        )

        if clean_name in self.downloaded_whisper_models:
            self.transcription.set_status(
                f"Выбрана модель '{clean_name}' (Локально)", STATUS_GREEN
            )
        else:
            self.transcription.set_status(
                f"Выбрана модель '{clean_name}' (Будет загружена при использовании)",
                STATUS_ORANGE,
            )

    def open_model_manager(self) -> None:
        """Открывает диспетчер моделей; повторный клик поднимает открытое окно."""
        if self.model_manager is not None and self.model_manager.winfo_exists():
            self.model_manager.lift()
            self.model_manager.focus_force()
            return
        self.model_manager = gui.ModelManagerWindow(
            self,
            downloaded_models=self.downloaded_whisper_models,
            on_models_changed=self.refresh_whisper_models,
            on_busy_changed=self.set_download_active,
            on_closed=self._on_model_manager_closed,
        )

    def _on_model_manager_closed(self) -> None:
        """Забывает ссылку на закрытый диспетчер моделей."""
        self.model_manager = None

    # ------------------------------------------------------------------
    # блокировка элементов управления
    # ------------------------------------------------------------------

    def set_download_active(self, active: bool) -> None:
        """Блокирует управление транскрибацией на время скачивания модели."""
        self._download_active = active
        self._apply_controls()

    def _set_transcription_active(self, active: bool) -> None:
        """Помечает, что идёт распознавание, и обновляет кнопки."""
        self._transcription_active = active
        self._apply_controls()

    def _apply_controls(self) -> None:
        """Согласует состояние кнопок с текущими фоновыми задачами."""
        busy = self._download_active or self._transcription_active
        state = "disabled" if busy else "normal"
        self.transcription.btn_load_audio.configure(state=state)
        self.whisper_menu.configure(state=state)
        # Кнопку записи нельзя блокировать во время записи: ею же её и останавливают.
        if self.recorder.is_recording:
            self.transcription.btn_record_mic.configure(state="normal")
        else:
            self.transcription.btn_record_mic.configure(state=state)

    # ------------------------------------------------------------------
    # запись с микрофона
    # ------------------------------------------------------------------

    def toggle_recording(self) -> None:
        """Переключает запись: первый клик начинает, второй останавливает."""
        if self.recorder.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        """Начинает запись. Модель загрузится позже, уже в рабочем потоке."""
        if self.recorder.is_recording:
            return
        try:
            self.recorder.start()
        except ww.RecorderError as exc:
            logger.error("не удалось начать запись: %s", exc)
            self.transcription.set_status(f"Ошибка записи: {exc}", COLOR_RED)
            return

        self.transcription.set_recording(True)
        self.transcription.set_status(
            "🔴 Запись идёт... Нажмите «Остановить запись», когда закончите",
            COLOR_RED,
        )
        self.transcription.clear_transcript()
        self._apply_controls()

    def stop_recording(self) -> None:
        """Останавливает запись и отправляет WAV на распознавание."""
        self.transcription.set_recording(False)
        try:
            temp_path = self.recorder.stop()
        except ww.RecorderError as exc:
            logger.error("не удалось сохранить запись: %s", exc)
            self.transcription.set_status(f"Ошибка сохранения аудио: {exc}", COLOR_RED)
            self._apply_controls()
            return

        self._apply_controls()
        if temp_path is None:
            self.transcription.set_status("Аудио не записано", STATUS_GRAY)
            return
        logger.info(
            "запись завершена: %.1f с, %d кадров в файле",
            self.recorder.duration,
            self.recorder.frames_written,
        )
        self._submit_transcription(temp_path, is_temp=True)

    def _on_recording_tick(self, seconds: float) -> None:
        """Вызывается потоком-писателем рекордера раз в секунду."""
        self.update_status(f"Запись идёт... {int(seconds)} сек")

    # ------------------------------------------------------------------
    # транскрибация
    # ------------------------------------------------------------------

    def start_transcription_thread(self) -> None:
        """Спрашивает файл и ставит его в очередь на распознавание."""
        file_path = fd.askopenfilename(
            title="Выберите аудиофайл",
            filetypes=[
                ("Аудио файлы", "*.mp3 *.wav *.ogg *.flac *.m4a"),
                ("Все файлы", "*.*"),
            ],
        )
        if not file_path:
            self.transcription.set_status("Файл не выбран", STATUS_GRAY)
            return

        self.transcription.clear_transcript()
        self._submit_transcription(Path(file_path), is_temp=False)

    def _submit_transcription(self, audio_path: Path, is_temp: bool) -> None:
        """
        Передаёт файл рабочему потоку.

        Модель загрузится внутри задачи, поэтому интерфейс не замирает
        даже при первой загрузке весов `large`.
        """
        self._set_transcription_active(True)
        self._pending_temp = audio_path if is_temp else None
        self.transcription.set_status(f"Обработка: {audio_path.name}", COLOR_ACCENT)
        self.transcription.start_progress_animation()
        logger.info("задача распознавания: %s (временный=%s)", audio_path, is_temp)
        self.worker.submit(
            audio_path,
            on_done=lambda text, path: self.after(
                0, self._on_transcription_done, text, path, is_temp
            ),
            on_error=lambda exc: self.after(
                0, self._on_transcription_failed, exc, audio_path, is_temp
            ),
            on_status=lambda message: self.after(0, self.update_status, message),
        )

    def _on_transcription_done(self, text: str, path: Path, is_temp: bool) -> None:
        """Успешное распознавание: показываем текст и убираем временный файл."""
        self.transcription.show_transcript(text or "(речь не распознана)")
        self.transcription.stop_progress_animation(1.0)
        self.transcription.set_status("Готово", STATUS_GREEN)
        if is_temp:
            ww.cleanup_temp_file(path)
            self._pending_temp = None
        self._set_transcription_active(False)
        logger.info("распознавание завершено, символов: %d", len(text))
        self._save_to_history(text)

    def _on_transcription_failed(self, exc: BaseException, path: Path, is_temp: bool) -> None:
        """Ошибка распознавания: сообщаем и всё равно чистим временный файл."""
        if is_temp:
            ww.cleanup_temp_file(path)
            self._pending_temp = None
        self.transcription.stop_progress_animation(0)
        self.transcription.set_status(f"Ошибка транскрибации: {exc}", COLOR_RED)
        self._set_transcription_active(False)
        logger.error("ошибка распознавания: %s", exc)

    # ------------------------------------------------------------------
    # обновление интерфейса (безопасно из рабочих потоков)
    # ------------------------------------------------------------------

    def update_progressbar(self, value: float) -> None:
        """Устанавливает значение прогресс-бара из рабочего потока."""
        self.after(0, lambda: self.transcription.set_progress(value))

    def update_status(self, message: str) -> None:
        """Показывает статус из рабочего потока."""
        self.after(0, lambda: self.transcription.set_status(message))


def _selftest_audio_path(argv: list[str]) -> Path | None:
    """Возвращает путь к аудио из аргумента ``--selftest-audio`` (если он есть)."""
    for index, arg in enumerate(argv):
        if arg == "--selftest-audio" and index + 1 < len(argv):
            return Path(argv[index + 1])
        if arg.startswith("--selftest-audio="):
            return Path(arg.partition("=")[2])
    return None


def _selftest_model() -> str | None:
    """Выбирает самую маленькую уже скачанную модель, ничего не скачивая."""
    available = dm.get_downloaded_whisper_models()
    for name in dm.WHISPER_MODELS:
        if name in available:
            return name
    return None


def _run_selftest(audio_path: Path | None) -> int:
    """
    Диагностический прогон собранного приложения без участия пользователя.

    Проверяет импорт всех слоёв (в том числе torch и Whisper), создание
    главного окна, проверки окружения и — если передан WAV — реальное
    распознавание скачанной моделью. Результат пишется в обычный журнал,
    код возврата: 0 — успех, 1 — ошибка, 2 — нет данных для распознавания.
    """
    log_path = app_settings.setup_logging(console=not getattr(sys, "frozen", False))
    logger.info(
        "SELFTEST: старт (frozen=%s, exe=%s, _MEIPASS=%s, журнал=%s)",
        getattr(sys, "frozen", False),
        sys.executable,
        getattr(sys, "_MEIPASS", "-"),
        log_path,
    )
    try:
        results = preflight.run_checks()
        for result in results:
            logger.info(
                "SELFTEST: проверка «%s»: %s — %s",
                result.name,
                "ok" if result.ok else "нет",
                result.detail,
            )

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        config = app_settings.Settings.load()
        app = NordScribeApp(config)
        app.update_idletasks()
        app.update()
        logger.info("SELFTEST: главное окно построено (%s)", app.geometry())
        app.destroy()
        logger.info("SELFTEST: главное окно закрыто")

        if audio_path is None:
            logger.info("SELFTEST: OK (распознавание пропущено: не передан WAV)")
            return 0
        if not audio_path.is_file():
            logger.error("SELFTEST: файл не найден: %s", audio_path)
            return 2
        model_name = _selftest_model()
        if model_name is None:
            logger.warning("SELFTEST: нет скачанных моделей — распознавание пропущено")
            return 2

        worker = ww.WhisperWorker(
            model_name_provider=lambda: model_name,
            language="en",
            verify_cache=False,
        )
        try:
            text = worker.transcribe(
                audio_path,
                on_status=lambda message: logger.info("SELFTEST: %s", message),
            )
        finally:
            worker.shutdown(wait=True)
        logger.info("SELFTEST: модель %s, символов %d: %r", model_name, len(text), text[:200])
        if not text.strip():
            logger.error("SELFTEST: речь не распознана")
            return 1
        logger.info("SELFTEST: OK")
        return 0
    except Exception:
        logger.exception("SELFTEST: ОШИБКА")
        return 1


def main() -> None:
    """Настраивает журнал, читает настройки и запускает цикл обработки событий."""
    if "--selftest" in sys.argv[1:]:
        raise SystemExit(_run_selftest(_selftest_audio_path(sys.argv[1:])))

    log_path = app_settings.setup_logging(console=not getattr(sys, "frozen", False))
    logger.info("NordScribe запускается (журнал: %s)", log_path)

    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")

    config = app_settings.Settings.load()
    logger.info(
        "настройки: модель=%s, язык=%s, тишина=%s",
        config.model,
        config.language,
        config.trim_silence,
    )

    app = NordScribeApp(config)
    app.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
