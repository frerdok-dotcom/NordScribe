"""Filesystem locations used by NordScribe.

The application keeps its own files in ``~/.nordscribe`` and uses
``~/.cache/whisper`` because that is where ``whisper.load_model()`` looks for
weights. Paths are module constants so tests can patch one place.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "APP_DIR",
    "LOG_PATH",
    "SETTINGS_PATH",
    "WHISPER_CACHE_DIR",
]

#: Per-user application directory: settings, history and the log live here.
APP_DIR: Path = Path.home() / ".nordscribe"

#: User preferences (JSON).
SETTINGS_PATH: Path = APP_DIR / "settings.json"

#: Rotating application log.
LOG_PATH: Path = APP_DIR / "nordscribe.log"

#: Whisper's own model cache. Do not move: the library reads exactly this path.
WHISPER_CACHE_DIR: Path = Path.home() / ".cache" / "whisper"
