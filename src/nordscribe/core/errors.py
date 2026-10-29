"""Application error hierarchy.

Services raise these exceptions instead of leaking ``urllib``, ``sqlite3`` or
``torch`` errors into the UI. The UI turns them into short status messages and
the log keeps the original traceback.
"""

from __future__ import annotations

__all__ = [
    "ChecksumError",
    "DownloadCancelled",
    "DownloadError",
    "IncompleteDownload",
    "NordScribeError",
    "RecorderError",
    "TranscriptionError",
    "UnknownModelError",
]


class NordScribeError(Exception):
    """Base class for expected, user-facing NordScribe failures."""


class RecorderError(NordScribeError):
    """The microphone stream could not be started or finished."""


class TranscriptionError(NordScribeError):
    """Whisper could not load a model or transcribe the audio."""


class DownloadError(NordScribeError):
    """Base class for model download failures."""


class DownloadCancelled(DownloadError):
    """The caller cancelled the download."""


class ChecksumError(DownloadError):
    """The downloaded bytes do not match the expected SHA-256."""


class IncompleteDownload(DownloadError):
    """The connection closed before the whole file was received."""


class UnknownModelError(NordScribeError):
    """The requested model is not part of the application catalog."""
