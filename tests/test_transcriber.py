"""Tests for the transcription worker: lazy loading, queueing, self-healing.

No Whisper weights and no network: a fake model loader is injected.
"""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from typing import Any

from nordscribe.core.errors import TranscriptionError
from nordscribe.core.models import MODEL_SPECS_BY_NAME
from nordscribe.infra.cache import ModelCache
from nordscribe.services.transcriber import Transcriber
from tests import make_temp_dir, remove_temp_dir


class FakeModel:
    """A Whisper model double."""

    def __init__(self, name: str) -> None:
        self.name = name

    def transcribe(self, path: str, **kwargs: object) -> dict[str, str]:
        time.sleep(0.02)
        return {"text": f"  текст от {self.name}  "}


class TranscriberCase(unittest.TestCase):
    """A transcriber with a temporary cache and a fake loader."""

    def setUp(self) -> None:
        self._tmp = make_temp_dir()
        self.cache = ModelCache(self._tmp)
        self.loads: list[str] = []
        self.selected = {"name": "base"}
        self.calls: list[dict[str, Any]] = []

        def loader(name: str) -> FakeModel:
            self.loads.append(name)
            if name == "broken":
                raise RuntimeError("нет весов")
            return FakeModel(name)

        self._loader = loader
        self.worker = self._make_worker()

    def _make_worker(self, **kwargs: object) -> Transcriber:
        return Transcriber(
            model_name_provider=lambda: self.selected["name"],
            cache=self.cache,
            model_loader=self._loader,
            **kwargs,  # type: ignore[arg-type]
        )

    def tearDown(self) -> None:
        self.worker.shutdown(wait=True)
        remove_temp_dir(self._tmp)

    def submit(self, name: str = "audio.wav") -> tuple[list[str], list[BaseException]]:
        results: list[str] = []
        errors: list[BaseException] = []
        self.worker.submit(
            Path(name),
            lambda text, path: results.append(text),
            errors.append,
        ).result(timeout=10)
        return results, errors


class LazyLoadingTest(TranscriberCase):
    """Model loading is lazy and cached."""

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
            Path("a.wav"),
            lambda text, path: results.append(text),
            lambda error: None,
            statuses.append,
        ).result(timeout=10)
        self.assertTrue(any("Загрузка модели" in item for item in statuses))
        self.assertTrue(any("распознавание" in item.lower() for item in statuses))


class CacheRepairTest(TranscriberCase):
    """A corrupt cached file is removed before loading."""

    def test_corrupt_cache_is_removed_and_reported(self) -> None:
        spec = MODEL_SPECS_BY_NAME["base"]
        path = self.cache.file_path(spec)
        path.write_bytes(b"truncated-weights")
        statuses: list[str] = []
        self.worker.submit(
            Path("a.wav"),
            lambda text, path: None,
            lambda error: None,
            statuses.append,
        ).result(timeout=10)
        self.assertFalse(path.exists())
        self.assertTrue(any("повреждён" in item for item in statuses))

    def test_verification_can_be_disabled(self) -> None:
        spec = MODEL_SPECS_BY_NAME["base"]
        path = self.cache.file_path(spec)
        path.write_bytes(b"anything")
        worker = self._make_worker(verify_cache=False)
        try:
            worker.submit(Path("a.wav"), lambda text, p: None, lambda e: None).result(timeout=10)
        finally:
            worker.shutdown(wait=True)
        self.assertTrue(path.exists())


class ErrorTest(TranscriberCase):
    """Errors are wrapped and delivered to ``on_error``."""

    def test_load_failure_is_reported(self) -> None:
        self.selected["name"] = "broken"
        results, errors = self.submit()
        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], TranscriptionError)
        self.assertIn("нет весов", str(errors[0]))

    def test_transcribe_failure_is_wrapped(self) -> None:
        class Broken(FakeModel):
            def transcribe(self, path: str, **kwargs: object) -> dict[str, str]:
                raise RuntimeError("битый файл")

        def loader(name: str) -> Broken:
            self.loads.append(name)
            return Broken(name)

        worker = Transcriber(
            model_name_provider=lambda: "base",
            cache=self.cache,
            model_loader=loader,
        )
        try:
            errors: list[BaseException] = []
            worker.submit(Path("a.wav"), lambda text, p: None, errors.append).result(timeout=10)
        finally:
            worker.shutdown(wait=True)
        self.assertIsInstance(errors[0], TranscriptionError)
        self.assertIn("битый файл", str(errors[0]))

    def test_submit_after_shutdown_raises(self) -> None:
        self.worker.shutdown(wait=True)
        with self.assertRaises(RuntimeError):
            self.worker.submit(Path("a.wav"), lambda text, path: None, lambda error: None)


class ModelCallTest(TranscriberCase):
    """Arguments passed to Whisper and serialization of jobs."""

    def test_verbose_is_none(self) -> None:
        calls: list[dict[str, object]] = []

        class Recording(FakeModel):
            def transcribe(self, path: str, **kwargs: object) -> dict[str, str]:
                calls.append(kwargs)
                return {"text": "готово"}

        def loader(name: str) -> Recording:
            return Recording(name)

        worker = Transcriber(
            model_name_provider=lambda: "base",
            cache=self.cache,
            model_loader=loader,
        )
        try:
            worker.submit(Path("a.wav"), lambda text, p: None, lambda e: None).result(timeout=10)
        finally:
            worker.shutdown(wait=True)
        self.assertIsNone(calls[0]["verbose"])
        self.assertEqual(calls[0]["task"], "transcribe")

    def test_tasks_are_serialised(self) -> None:
        order: list[str] = []

        class Slow(FakeModel):
            def transcribe(self, path: str, **kwargs: object) -> dict[str, str]:
                order.append(f"start:{path}")
                time.sleep(0.1)
                order.append(f"end:{path}")
                return {"text": "готово"}

        def loader(name: str) -> Slow:
            return Slow(name)

        worker = Transcriber(
            model_name_provider=lambda: "base",
            cache=self.cache,
            model_loader=loader,
        )
        try:
            futures = [
                worker.submit(Path(f"{index}.wav"), lambda text, p: None, lambda e: None)
                for index in range(3)
            ]
            for future in futures:
                future.result(timeout=10)
        finally:
            worker.shutdown(wait=True)
        self.assertEqual(
            order,
            [
                "start:0.wav",
                "end:0.wav",
                "start:1.wav",
                "end:1.wav",
                "start:2.wav",
                "end:2.wav",
            ],
        )
