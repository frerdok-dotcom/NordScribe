"""Microphone recording with streaming disk writes and silence trimming.

Audio callbacks never accumulate the whole recording in memory: PortAudio
pushes blocks into a queue and a writer thread writes them to a temporary WAV.
Leading and trailing silence is trimmed on the fly; internal pauses are kept
because Whisper needs the context.

This module does not import ``sounddevice`` at import time. The input stream is
created lazily in :meth:`AudioRecorder.start`, so a machine without PortAudio
can still import the app, show the window and transcribe files.
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
from pathlib import Path
from typing import Any, cast

import numpy as np

from nordscribe.core.errors import RecorderError

__all__ = ["DEFAULT_SAMPLE_RATE", "AudioRecorder", "cleanup_temp_file"]

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE = 16000

#: How many seconds of leading silence may be buffered before the first loud
#: block. This bounds memory on recordings that never contain speech.
_LEAD_IN_CAP_SECONDS = 5.0

#: Queue polling interval for the writer thread.
_QUEUE_TIMEOUT = 0.1


def cleanup_temp_file(path: Path | None) -> bool:
    """Delete a temporary recording.

    Returns:
        ``True`` when the file existed and was removed.
    """
    if path is None:
        return False
    try:
        Path(path).unlink()
        return True
    except OSError:
        return False


def _peak_to_db(peak: int) -> float:
    """Convert an int16 peak amplitude to dBFS; silence is ``-inf``."""
    if peak <= 0:
        return float("-inf")
    return 20.0 * math.log10(min(peak, 32768) / 32768.0)


def _default_stream_factory() -> Callable[..., Any]:
    """Import sounddevice only when recording actually starts."""
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover - depends on PortAudio
        raise RecorderError(f"sounddevice/PortAudio недоступен: {exc}") from exc
    return cast(Callable[..., Any], sd.InputStream)


class AudioRecorder:
    """Record from the default input device into a temporary WAV file."""

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        on_tick: Callable[[float], None] | None = None,
        stream_factory: Callable[..., Any] | None = None,
        trim_silence: bool = True,
        silence_threshold_db: float = -45.0,
        silence_margin_seconds: float = 0.3,
    ) -> None:
        self.sample_rate = sample_rate
        self._on_tick = on_tick
        self._stream_factory = stream_factory
        self._trim = trim_silence
        self._threshold_db = silence_threshold_db
        self._margin_frames = max(int(silence_margin_seconds * sample_rate), 0)
        self._lead_in_cap_frames = int(_LEAD_IN_CAP_SECONDS * sample_rate)

        self._queue: queue.Queue[tuple[bytes, int]] = queue.Queue()
        self._stream: Any = None
        self._writer: threading.Thread | None = None
        self._wave: wave.Wave_write | None = None
        self._temp_path: Path | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._write_error: BaseException | None = None

        self._pending: deque[tuple[bytes, int]] = deque()
        self._pending_frames = 0
        self._loud_seen = False
        self._frames_total = 0
        self._frames_written = 0
        self._last_reported_second = -1

    @property
    def is_recording(self) -> bool:
        """Whether input is currently being captured."""
        with self._lock:
            return self._stream is not None

    @property
    def duration(self) -> float:
        """Duration of the received audio in seconds."""
        if self.sample_rate <= 0:
            return 0.0
        return self._frames_total / self.sample_rate

    @property
    def frames_written(self) -> int:
        """Frames actually written after silence trimming."""
        return self._frames_written

    def start(self) -> None:
        """Open the WAV, start the writer and open the input stream.

        Raises:
            RecorderError: the file or input device could not be opened.
        """
        with self._lock:
            if self._stream is not None:
                return
            self._reset_state()
            self._open_sink()
            self._writer = threading.Thread(
                target=self.record_audio,
                name="nordscribe-recorder",
                daemon=True,
            )
            self._writer.start()
            try:
                stream = self._create_stream()
            except Exception as exc:
                self._stop_event.set()
                self._join_writer()
                self._close_sink()
                cleanup_temp_file(self._temp_path)
                self._temp_path = None
                raise RecorderError(str(exc)) from exc
            # Set before start(): the first PortAudio callback must see an
            # active stream, otherwise the first block is silently dropped.
            self._stream = stream

        try:
            stream.start()
        except Exception as exc:
            with self._lock:
                self._stream = None
            self._stop_event.set()
            self._join_writer()
            self._close_sink()
            cleanup_temp_file(self._temp_path)
            self._temp_path = None
            self._close_stream(stream)
            raise RecorderError(str(exc)) from exc

    def _create_stream(self) -> Any:
        factory = self._stream_factory or _default_stream_factory()
        return factory(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            callback=self.audio_callback,
        )

    def _join_writer(self) -> None:
        writer = self._writer
        self._writer = None
        if writer is not None:
            writer.join(timeout=1.0)

    def _close_stream(self, stream: Any) -> None:
        for method in ("stop", "close"):
            action = getattr(stream, method, None)
            if callable(action):
                try:
                    action()
                except Exception as exc:  # pragma: no cover - PortAudio
                    logger.debug("error while closing input stream: %s", exc)

    def _reset_state(self) -> None:
        """Prepare fields for a new recording."""
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
        """Create and open the temporary WAV file."""
        handle, name = tempfile.mkstemp(suffix=".wav", prefix="nordscribe_")
        os.close(handle)
        self._temp_path = Path(name)
        try:
            # The handle lives across calls and is closed by _close_sink, so a
            # context manager cannot be used here.
            self._wave = wave.open(str(self._temp_path), "wb")  # noqa: SIM115
            self._wave.setnchannels(1)
            self._wave.setsampwidth(2)  # int16
            self._wave.setframerate(self.sample_rate)
        except Exception as exc:
            self._wave = None
            cleanup_temp_file(self._temp_path)
            self._temp_path = None
            raise RecorderError(f"не удалось создать файл записи: {exc}") from exc

    def _close_sink(self) -> None:
        """Close the WAV file, writing a correct header."""
        wave_out, self._wave = self._wave, None
        if wave_out is not None:
            try:
                wave_out.close()
            except Exception as exc:  # pragma: no cover - filesystem
                logger.warning("error closing WAV: %s", exc)

    def audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        """PortAudio callback: copy the block and enqueue it.

        No disk I/O and no long lock is held here; the writer thread does the
        actual work.
        """
        with self._lock:
            if self._stream is None:
                return
        try:
            mono = indata[:, 0] if indata.ndim > 1 else indata
            peak = int(np.abs(mono).max()) if mono.size else 0
            payload = mono.astype(np.int16, copy=True).tobytes()
        except Exception:
            logger.exception("cannot process an audio block; dropping it")
            return
        self._queue.put((payload, peak))

    def record_audio(self) -> None:
        """Writer thread: drain the queue, trim silence and update ticks."""
        while True:
            try:
                block, peak = self._queue.get(timeout=_QUEUE_TIMEOUT)
            except queue.Empty:
                if self._stop_event.is_set() and self._queue.empty():
                    break
                self._report_tick()
                continue

            try:
                self._write_block(block, peak)
            except Exception as exc:
                self._write_error = exc
                logger.exception("error writing audio to disk")
                break
            self._report_tick()

        self._flush_tail()
        self._close_sink()

    def _report_tick(self) -> None:
        """Report duration at most once per second.

        The callback is user code (in the app it posts an update to the UI
        queue). An exception there must not kill the writer thread, otherwise
        the recording is silently lost.
        """
        if self._on_tick is None:
            return
        second = int(self.duration)
        if second == self._last_reported_second:
            return
        self._last_reported_second = second
        try:
            self._on_tick(self.duration)
        except Exception:
            logger.exception("on_tick callback failed; recording continues")

    def _write_block(self, block: bytes, peak: int) -> None:
        """Write a block, dropping silence at the edges when trimming is on."""
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
            # Speech: flush the buffered lead-in and write the block.
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

    def _dump_pending(self, limit: int | None = None) -> None:
        """Write buffered silence, optionally no more than ``limit`` frames."""
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
        """Write the tail when the recording ends."""
        if not self._trim or not self._loud_seen:
            # Trimming is off or there was no speech at all: keep the audio.
            self._dump_pending()
            return
        self._dump_pending(limit=self._margin_frames)
        self._pending.clear()
        self._pending_frames = 0

    def stop(self) -> Path | None:
        """Stop recording and return the finished WAV path.

        The method is idempotent: a second call (or a call before ``start``)
        returns ``None``. Ownership of the returned file passes to the caller.

        Raises:
            RecorderError: writing to disk failed.
        """
        with self._lock:
            stream, self._stream = self._stream, None
            writer, self._writer = self._writer, None
            path, self._temp_path = self._temp_path, None

        if stream is None and writer is None:
            return None

        self._stop_event.set()
        if stream is not None:
            self._close_stream(stream)
        if writer is not None:
            writer.join(timeout=2.0)
            if writer.is_alive():  # pragma: no cover - defensive
                logger.warning("recorder writer did not stop within 2 seconds")

        self._close_sink()

        if self._write_error is not None:
            error, self._write_error = self._write_error, None
            cleanup_temp_file(path)
            raise RecorderError(f"не удалось сохранить запись: {error}")

        if self._frames_written == 0:
            cleanup_temp_file(path)
            return None
        return path
