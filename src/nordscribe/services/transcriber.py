"""Whisper model ownership and serialized transcription.

The transcriber loads weights lazily and runs jobs on one daemon worker
thread. A single worker serializes CPU-heavy transcriptions and a daemon
thread means closing the window never waits minutes for a running job.

Heavy imports (``whisper`` -> ``torch``) happen only inside the default model
loader, so importing this module is cheap.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future, InvalidStateError
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nordscribe.core.errors import TranscriptionError
from nordscribe.core.models import MODEL_SPECS_BY_NAME
from nordscribe.infra.cache import ModelCache

__all__ = [
    "DoneCallback",
    "ErrorCallback",
    "StatusCallback",
    "Transcriber",
]

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]
DoneCallback = Callable[[str, Path], None]
ErrorCallback = Callable[[BaseException], None]
ModelLoader = Callable[[str], Any]


def _load_whisper_model(name: str) -> Any:
    """Default loader: import Whisper and load the weights."""
    import whisper

    return whisper.load_model(name)


@dataclass(slots=True)
class _Job:
    audio_path: Path
    on_done: DoneCallback
    on_error: ErrorCallback
    on_status: StatusCallback | None
    future: Future[None]


class Transcriber:
    """Own a Whisper model and run transcription jobs in order."""

    def __init__(
        self,
        model_name_provider: Callable[[], str],
        language: str = "ru",
        verify_cache: bool = True,
        *,
        cache: ModelCache | None = None,
        model_loader: ModelLoader | None = None,
    ) -> None:
        self._model_name_provider = model_name_provider
        self._language = language
        self._verify_cache = verify_cache
        self._cache = cache or ModelCache()
        self._model_loader = model_loader or _load_whisper_model

        self._model: Any = None
        self._loaded_name: str | None = None
        self._model_lock = threading.Lock()

        self._queue: queue.Queue[_Job | None] = queue.Queue()
        self._state_lock = threading.Lock()
        self._closed = False
        self._worker = threading.Thread(
            target=self._run,
            name="nordscribe-transcriber",
            daemon=True,
        )
        self._worker.start()

    @property
    def loaded_model_name(self) -> str | None:
        """Name of the loaded model, or ``None`` before the first job."""
        return self._loaded_name

    def ensure_model(self, on_status: StatusCallback | None = None) -> str:
        """Load the selected model if it is not loaded already.

        This is blocking and must run on the worker thread.

        Raises:
            TranscriptionError: the model could not be loaded.
        """
        with self._model_lock:
            name = self._model_name_provider()
            if self._model is not None and self._loaded_name == name:
                return name
            if self._verify_cache:
                self._repair_cache(name, on_status)
            self._notify(on_status, f"Загрузка модели Whisper ({name})...")
            try:
                model = self._model_loader(name)
            except Exception as exc:
                raise TranscriptionError(f"не удалось загрузить модель '{name}': {exc}") from exc
            self._model = model
            self._loaded_name = name
            return name

    def _repair_cache(self, name: str, on_status: StatusCallback | None) -> None:
        """Delete a corrupt model file so Whisper downloads it again."""
        spec = MODEL_SPECS_BY_NAME.get(name)
        if spec is None:
            return
        try:
            valid = self._cache.is_valid(spec)
        except OSError as exc:
            logger.warning("cannot verify model '%s': %s", name, exc)
            return
        if valid is not False:
            return
        logger.warning("model file '%s' is corrupt; deleting and re-downloading", name)
        self._notify(on_status, f"Файл модели '{name}' повреждён, скачиваю заново...")
        removed = self._cache.remove(spec)
        logger.info("removed corrupt files: %s", [item.path.name for item in removed])

    def transcribe(
        self,
        audio_path: Path,
        on_status: StatusCallback | None = None,
    ) -> str:
        """Transcribe one file (blocking; call from the worker thread).

        Raises:
            TranscriptionError: model loading or transcription failed.
        """
        self.ensure_model(on_status)
        self._notify(on_status, "Начинаю распознавание...")
        try:
            result = self._model.transcribe(
                str(audio_path),
                language=self._language,
                task="transcribe",
                # None (not False) disables both segment printing and tqdm.
                # In a windowed build sys.stderr is None and tqdm would raise.
                verbose=None,
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
        on_status: StatusCallback | None = None,
    ) -> Future[None]:
        """Queue a transcription job.

        Callbacks run on the worker thread. The UI must post them to its own
        dispatcher, not touch widgets directly.
        """
        with self._state_lock:
            if self._closed:
                raise RuntimeError("Transcriber is already shut down")
            job = _Job(
                audio_path=Path(audio_path),
                on_done=on_done,
                on_error=on_error,
                on_status=on_status,
                future=Future(),
            )
            self._queue.put(job)
        return job.future

    def _run(self) -> None:
        """Worker loop: one job at a time, callbacks isolated from failures."""
        while True:
            job = self._queue.get()
            if job is None:
                break
            if job.future.cancelled():
                continue
            try:
                text = self.transcribe(job.audio_path, job.on_status)
            except BaseException as exc:
                self._finish(job.future)
                try:
                    job.on_error(exc)
                except Exception:
                    logger.exception("on_error callback failed")
            else:
                self._finish(job.future)
                try:
                    job.on_done(text, job.audio_path)
                except Exception:
                    logger.exception("on_done callback failed")

    @staticmethod
    def _finish(future: Future[None]) -> None:
        """Mark a future done without raising when it was cancelled."""
        with suppress(InvalidStateError):
            future.set_result(None)

    @staticmethod
    def _notify(on_status: StatusCallback | None, message: str) -> None:
        if on_status is None:
            return
        try:
            on_status(message)
        except Exception:
            logger.exception("on_status callback failed")

    def shutdown(self, wait: bool = False) -> None:
        """Stop the worker; queued jobs are cancelled.

        A running transcription cannot be interrupted safely inside torch.
        Because the thread is a daemon, process exit does not wait for it.
        """
        with self._state_lock:
            if self._closed:
                return
            self._closed = True

        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                break
            if job is not None:
                job.future.cancel()
        self._queue.put(None)
        if wait:
            self._worker.join(timeout=5.0)
