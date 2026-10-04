"""Тесты слоя аудио-обработки: обрезка тишины, рекордер, воркер Whisper.

Микрофон и модель подменяются: тесты не требуют ни устройства ввода,
ни скачанных весов, ни сети.
"""

from __future__ import annotations

import time
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

import download_manager as dm
import whisper_worker as ww
from tests import make_temp_dir, remove_temp_dir

SR = 16000


def silence(seconds: float, amplitude: int = 0) -> np.ndarray:
    """Возвращает блок int16 нужной длины."""
    return np.full(int(seconds * SR), amplitude, dtype=np.int16)


def tone(seconds: float, amplitude: int = 8000) -> np.ndarray:
    """Возвращает «звучащий» блок: постоянная амплитуда выше порога."""
    return np.full(int(seconds * SR), amplitude, dtype=np.int16)


def frames_in(path: Path) -> int:
    """Читает WAV и возвращает число кадров — заодно проверяет заголовок."""
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes()


class TrimSilenceBoundsTest(unittest.TestCase):
    """Чистая функция поиска границ речи."""

    def test_trims_leading_and_trailing(self) -> None:
        data = np.concatenate([silence(1.0), tone(1.0), silence(1.0)])
        start, end = ww.trim_silence_bounds(data, sample_rate=SR, margin_seconds=0.0)
        self.assertAlmostEqual(start / SR, 1.0, places=1)
        self.assertAlmostEqual(end / SR, 2.0, places=1)

    def test_margin_keeps_context(self) -> None:
        data = np.concatenate([silence(1.0), tone(1.0), silence(1.0)])
        start, end = ww.trim_silence_bounds(data, sample_rate=SR, margin_seconds=0.3)
        self.assertLess(start / SR, 1.0)
        self.assertGreater(end / SR, 2.0)

    def test_all_silence_gives_empty_range(self) -> None:
        self.assertEqual(
            ww.trim_silence_bounds(silence(2.0), sample_rate=SR), (0, 0)
        )

    def test_all_sound_is_kept_whole(self) -> None:
        data = tone(1.0)
        self.assertEqual(ww.trim_silence_bounds(data, sample_rate=SR), (0, data.size))

    def test_empty_input(self) -> None:
        self.assertEqual(ww.trim_silence_bounds(np.array([], dtype=np.int16)), (0, 0))

    def test_internal_pauses_are_not_cut(self) -> None:
        # Разрывать речь нельзя: Whisper теряет контекст.
        data = np.concatenate([tone(0.5), silence(2.0), tone(0.5)])
        start, end = ww.trim_silence_bounds(data, sample_rate=SR, margin_seconds=0.0)
        self.assertEqual(start, 0)
        self.assertAlmostEqual(end / SR, 3.0, places=1)


class FakeStream:
    """Заглушка входного потока sounddevice."""

    def __init__(self, **kwargs: object) -> None:
        self.callback = kwargs["callback"]
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True

    def feed(self, samples: np.ndarray) -> None:
        """Отдаёт блок так, как это сделал бы PortAudio."""
        self.callback(samples.reshape(-1, 1), samples.size, None, None)


class RecorderCase(unittest.TestCase):
    """Базовый класс с подменённым потоком ввода."""

    def setUp(self) -> None:
        self.streams: list[FakeStream] = []

        def factory(**kwargs: object) -> FakeStream:
            stream = FakeStream(**kwargs)
            self.streams.append(stream)
            return stream

        self.factory = factory
        self.ticks: list[float] = []

    def make(self, **kwargs: object) -> ww.AudioRecorder:
        return ww.AudioRecorder(
            sample_rate=SR, on_tick=self.ticks.append,
            stream_factory=self.factory, **kwargs,  # type: ignore[arg-type]
        )

    @property
    def stream(self) -> FakeStream:
        return self.streams[0]


class AudioRecorderTest(RecorderCase):
    """Потоковая запись на диск, без накопления в памяти."""

    def test_streams_frames_to_disk(self) -> None:
        recorder = self.make(trim_silence=False)
        recorder.start()
        self.assertTrue(recorder.is_recording)
        for _ in range(5):
            self.stream.feed(tone(0.1))
        path = recorder.stop()
        self.assertIsNotNone(path)
        self.assertEqual(frames_in(path), 5 * SR // 10)
        self.assertFalse(recorder.is_recording)
        ww.cleanup_temp_file(path)

    def test_no_frames_gives_none_and_leaves_nothing(self) -> None:
        recorder = self.make(trim_silence=False)
        recorder.start()
        self.assertIsNone(recorder.stop())

    def test_trim_removes_silence_edges(self) -> None:
        recorder = self.make(trim_silence=True, silence_threshold_db=-45.0)
        recorder.start()
        for _ in range(10):
            self.stream.feed(silence(0.1))       # 1.0 с тишины в начале
        for _ in range(10):
            self.stream.feed(tone(0.1))          # 1.0 с речи
        for _ in range(20):
            self.stream.feed(silence(0.1))       # 2.0 с тишины в конце
        path = recorder.stop()
        written = frames_in(path)
        self.assertGreater(recorder.frames_written, 0)
        # Осталось заметно меньше исходных 4 секунд, но речь целиком на месте.
        self.assertLess(written, int(2.5 * SR))
        self.assertGreaterEqual(written, int(1.0 * SR))
        ww.cleanup_temp_file(path)

    def test_fully_silent_recording_is_not_lost(self) -> None:
        recorder = self.make(trim_silence=True)
        recorder.start()
        for _ in range(5):
            self.stream.feed(silence(0.1))
        path = recorder.stop()
        self.assertIsNotNone(path, "запись без речи не должна исчезать целиком")
        self.assertGreater(frames_in(path), 0)
        ww.cleanup_temp_file(path)

    def test_ticks_are_throttled_to_seconds(self) -> None:
        recorder = self.make(trim_silence=False)
        recorder.start()
        for _ in range(20):
            self.stream.feed(tone(0.1))
        time.sleep(0.5)
        recorder.stop()
        # 2 секунды аудио не должны дать два десятка обновлений статуса.
        self.assertLessEqual(len(self.ticks), 3)
        self.assertGreaterEqual(len(self.ticks), 1)

    def test_closes_input_stream(self) -> None:
        recorder = self.make(trim_silence=False)
        recorder.start()
        self.stream.feed(tone(0.1))
        recorder.stop()
        self.assertTrue(self.stream.stopped)
        self.assertTrue(self.stream.closed)

    def test_failed_stream_start_cleans_up(self) -> None:
        def broken(**kwargs: object) -> FakeStream:
            raise RuntimeError("устройство занято")

        recorder = ww.AudioRecorder(sample_rate=SR, stream_factory=broken)
        with self.assertRaises(ww.RecorderError) as ctx:
            recorder.start()
        self.assertIn("устройство занято", str(ctx.exception))
        self.assertFalse(recorder.is_recording)
        self.assertIsNone(recorder.stop())

    def test_double_start_is_ignored(self) -> None:
        recorder = self.make(trim_silence=False)
        recorder.start()
        recorder.start()
        self.assertEqual(len(self.streams), 1)
        recorder.stop()

    def test_cleanup_helper(self) -> None:
        recorder = self.make(trim_silence=False)
        recorder.start()
        self.stream.feed(tone(0.1))
        path = recorder.stop()
        self.assertTrue(ww.cleanup_temp_file(path))
        self.assertFalse(path.exists())
        self.assertFalse(ww.cleanup_temp_file(None))


class FakeModel:
    """Заглушка модели Whisper."""

    def __init__(self, name: str) -> None:
        self.name = name

    def transcribe(self, path: str, **kwargs: object) -> dict[str, str]:
        time.sleep(0.05)
        return {"text": f"  текст от {self.name}  "}


class WhisperWorkerTest(unittest.TestCase):
    """Ленивая загрузка, сериализация и самолечение кэша."""

    def setUp(self) -> None:
        self._tmp = make_temp_dir()
        self.cache = self._tmp
        self._cache_patch = mock.patch.object(dm, "WHISPER_CACHE_DIR", self.cache)
        self._cache_patch.start()
        self.loads: list[str] = []
        self._load_patch = mock.patch.object(
            ww.whisper, "load_model",
            side_effect=lambda name: (self.loads.append(name), FakeModel(name))[1],
        )
        self._load_patch.start()
        self.selected = {"name": "base"}
        self.worker = ww.WhisperWorker(
            model_name_provider=lambda: self.selected["name"]
        )

    def tearDown(self) -> None:
        self.worker.shutdown(wait=True)
        self._load_patch.stop()
        self._cache_patch.stop()
        remove_temp_dir(self._tmp)

    def submit(self, name: str = "audio.wav") -> tuple[list, list]:
        results: list[str] = []
        errors: list[BaseException] = []
        self.worker.submit(
            Path(name), lambda text, path: results.append(text), errors.append
        ).result(timeout=10)
        return results, errors

    def test_model_is_loaded_lazily(self) -> None:
        self.assertIsNone(self.worker.loaded_model_name)
        self.assertEqual(self.loads, [])
        self.submit()
        self.assertEqual(self.loads, ["base"])
        self.assertEqual(self.worker.loaded_model_name, "base")

    def test_model_is_loaded_once(self) -> None:
        self.submit("a.wav")
        self.submit("b.wav")
        self.assertEqual(self.loads, ["base"])

    def test_switching_model_reloads(self) -> None:
        self.submit("a.wav")
        self.selected["name"] = "small"
        results, _ = self.submit("b.wav")
        self.assertEqual(self.loads, ["base", "small"])
        self.assertEqual(results[-1], "текст от small")

    def test_status_messages_reach_caller(self) -> None:
        statuses: list[str] = []
        results: list[str] = []
        self.worker.submit(
            Path("a.wav"), lambda t, p: results.append(t), lambda e: None,
            statuses.append,
        ).result(timeout=10)
        self.assertTrue(any("Загрузка модели" in s for s in statuses))
        self.assertTrue(any("распознавание" in s.lower() for s in statuses))

    def test_corrupt_cache_is_removed_and_reported(self) -> None:
        (self.cache / "base.pt").write_bytes(b"truncated-weights")
        statuses: list[str] = []
        self.worker.submit(
            Path("a.wav"), lambda t, p: None, lambda e: None, statuses.append
        ).result(timeout=10)
        self.assertFalse((self.cache / "base.pt").exists())
        self.assertTrue(any("повреждён" in s for s in statuses))

    def test_valid_cache_is_kept(self) -> None:
        payload = b"valid-weights"
        (self.cache / "base.pt").write_bytes(payload)
        import hashlib

        digest = hashlib.sha256(payload).hexdigest()
        with mock.patch.object(dm, "expected_sha256", return_value=digest):
            self.worker.submit(Path("a.wav"), lambda t, p: None, lambda e: None).result(
                timeout=10
            )
        self.assertTrue((self.cache / "base.pt").exists())

    def test_verification_can_be_disabled(self) -> None:
        (self.cache / "base.pt").write_bytes(b"anything")
        worker = ww.WhisperWorker(
            model_name_provider=lambda: "base", verify_cache=False
        )
        worker.submit(Path("a.wav"), lambda t, p: None, lambda e: None).result(
            timeout=10
        )
        self.assertTrue((self.cache / "base.pt").exists())
        worker.shutdown(wait=True)

    def test_load_failure_is_reported(self) -> None:
        with mock.patch.object(
            ww.whisper, "load_model", side_effect=RuntimeError("нет весов")
        ):
            results: list[str] = []
            errors: list[BaseException] = []
            self.worker.submit(
                Path("a.wav"), lambda t, p: results.append(t), errors.append
            ).result(timeout=10)
        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ww.TranscriptionError)
        self.assertIn("нет весов", str(errors[0]))

    def test_transcribe_failure_is_wrapped(self) -> None:
        class Broken(FakeModel):
            def transcribe(self, path: str, **kwargs: object) -> dict[str, str]:
                raise RuntimeError("битый файл")

        with mock.patch.object(ww.whisper, "load_model", return_value=Broken("x")):
            errors: list[BaseException] = []
            self.worker.submit(
                Path("a.wav"), lambda t, p: None, errors.append
            ).result(timeout=10)
        self.assertIsInstance(errors[0], ww.TranscriptionError)
        self.assertIn("битый файл", str(errors[0]))

    def test_tasks_are_serialised(self) -> None:
        order: list[str] = []

        class Slow(FakeModel):
            def transcribe(self, path: str, **kwargs: object) -> dict[str, str]:
                order.append(f"start:{path}")
                time.sleep(0.1)
                order.append(f"end:{path}")
                return {"text": "готово"}

        with mock.patch.object(ww.whisper, "load_model", return_value=Slow("x")):
            futures = [
                self.worker.submit(Path(f"{i}.wav"), lambda t, p: None, lambda e: None)
                for i in range(3)
            ]
            for future in futures:
                future.result(timeout=10)
        # Ни одна задача не началась, пока не закончилась предыдущая.
        self.assertEqual(
            order,
            ["start:0.wav", "end:0.wav", "start:1.wav", "end:1.wav",
             "start:2.wav", "end:2.wav"],
        )


if __name__ == "__main__":
    unittest.main()
