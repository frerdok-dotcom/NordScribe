"""NordScribe — local, private audio transcription with OpenAI Whisper.

The package is split into four layers:

``core``
    Model metadata, formatting and errors. No I/O and no GUI.
``infra``
    Settings, logging, SQLite history and the Whisper model cache.
``services``
    Recorder, transcriber, downloader, model service and environment checks.
``ui``
    CustomTkinter widgets and the main window.

Only :mod:`nordscribe.app` and :mod:`nordscribe.selftest` wire the layers
together. See ``docs/ARCHITECTURE.md`` for the dependency rules.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.1.0"
