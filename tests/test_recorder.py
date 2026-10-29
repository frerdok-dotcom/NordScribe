"""Tests for the streaming audio recorder (no microphone required)."""

from __future__ import annotations

import time
import unittest
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

from nordscribe.core.errors import RecorderError
from nordscribe.services.recorder import AudioRecorder, cleanup_temp_file
from tests import make_temp_dir, remove_temp_dir

SR = 16000


def silence(seconds: float, amplitude: int = 0) -> np.ndarray:
    """An int16 block of silence."""
    return np.full(int(seconds * SR), amplitude, dtype=np.int16)


def tone(seconds: float, amplitude: int = 8000) -> np.ndarray:
    """An int16 block above the silence threshold."""
    return np.full(int(seconds * SR), amplitude, dtype=np.int16)


def frames_in(path: Path) -> int:
    """Read a WAV and return its frame count (also validates the header)."""
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes()


class FakeStream:
    """Stand-in for ``sounddevice.InputStream``."""

    def __init__(self, **kwargs: object) -> None:
        self.callback: Callable[..., None] = kwargs["callback"]  # type: ignore[assignment]
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
        self.callback(samples.reshape(-1, 1), samples.size, None, None)


class ImmediateStream(FakeStream):
    """A stream that delivers a block during ``start()``."""

    def __init__(self, block: np.ndarray, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._block = block

    def start(self) -> None:
        super().start()
        self.feed(self._block)


class RecorderCase(unittest.TestCase):
    """Base class with a fake input stream."""

    def setUp(self) -> None:
        self._tmp = make_temp_dir()
        self.streams: list[FakeStream] = []

        def factory(**kwargs: object) -> FakeStream:
            stream = FakeStream(**kwargs)
            self.streams.append(stream)
            return stream

        self.factory = factory
        self.ticks: list[float] = []

    def tearDown(self) -> None:
        remove_temp_dir(self._tmp)

    def make(self, **kwargs: object) -> AudioRecorder:
        return AudioRecorder(
            sample_rate=SR,
            on_tick=self.ticks.append,
            stream_factory=self.factory,
            **kwargs,  # type: ignore[arg-type]
        )

    @property
    def stream(self) -> FakeStream:
        return self.streams[0]


class AudioRecorderTest(RecorderCase):
    """Streaming to disk without accumulating frames in memory."""

    def test_streams_frames_to_disk(self) -> None:
        recorder = self.make(trim_silence=False)
        recorder.start()
        self.assertTrue(recorder.is_recording)
        for _ in range(5):
            self.stream.feed(tone(0.1))
        path = recorder.stop()
        self.assertIsNotNone(path)
        assert path is not None
        self.assertEqual(frames_in(path), 5 * SR // 10)
        self.assertFalse(recorder.is_recording)
        cleanup_temp_file(path)

    def test_no_frames_gives_none_and_leaves_nothing(self) -> None:
        recorder = self.make(trim_silence=False)
        recorder.start()
        self.assertIsNone(recorder.stop())

    def test_stop_is_idempotent(self) -> None:
        recorder = self.make(trim_silence=False)
        recorder.start()
        self.stream.feed(tone(0.1))
        first = recorder.stop()
        self.assertIsNotNone(first)
        self.assertIsNone(recorder.stop())
        cleanup_temp_file(first)

    def test_trim_removes_silence_edges(self) -> None:
        recorder = self.make(trim_silence=True, silence_threshold_db=-45.0)
        recorder.start()
        for _ in range(10):
            self.stream.feed(silence(0.1))
        for _ in range(10):
            self.stream.feed(tone(0.1))
        for _ in range(20):
            self.stream.feed(silence(0.1))
        path = recorder.stop()
        self.assertIsNotNone(path)
        assert path is not None
        written = frames_in(path)
        self.assertGreater(recorder.frames_written, 0)
        self.assertLess(written, int(2.5 * SR))
        self.assertGreaterEqual(written, int(1.0 * SR))
        cleanup_temp_file(path)

    def test_fully_silent_recording_is_not_lost(self) -> None:
        recorder = self.make(trim_silence=True)
        recorder.start()
        for _ in range(5):
            self.stream.feed(silence(0.1))
        path = recorder.stop()
        self.assertIsNotNone(path, "a silent recording must not disappear")
        assert path is not None
        self.assertGreater(frames_in(path), 0)
        cleanup_temp_file(path)

    def test_ticks_are_throttled_to_seconds(self) -> None:
        recorder = self.make(trim_silence=False)
        recorder.start()
        for _ in range(20):
            self.stream.feed(tone(0.1))
        time.sleep(0.5)
        recorder.stop()
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

        recorder = AudioRecorder(sample_rate=SR, stream_factory=broken)
        with self.assertRaises(RecorderError) as ctx:
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

    def test_first_callback_is_not_dropped(self) -> None:
        stream_holder: list[ImmediateStream] = []

        def factory(**kwargs: object) -> ImmediateStream:
            stream = ImmediateStream(tone(0.1), **kwargs)
            stream_holder.append(stream)
            return stream

        recorder = AudioRecorder(sample_rate=SR, stream_factory=factory, trim_silence=False)
        recorder.start()
        path = recorder.stop()
        self.assertIsNotNone(path)
        assert path is not None
        self.assertGreater(frames_in(path), 0)
        cleanup_temp_file(path)

    def test_cleanup_helper(self) -> None:
        recorder = self.make(trim_silence=False)
        recorder.start()
        self.stream.feed(tone(0.1))
        path = recorder.stop()
        assert path is not None
        self.assertTrue(cleanup_temp_file(path))
        self.assertFalse(path.exists())
        self.assertFalse(cleanup_temp_file(None))
