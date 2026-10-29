"""Application entry point and composition root."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from nordscribe import __version__
from nordscribe.cli import parse_args
from nordscribe.infra.cache import ModelCache
from nordscribe.infra.history import TranscriptionHistory
from nordscribe.infra.paths import WHISPER_CACHE_DIR
from nordscribe.infra.settings import Settings, setup_logging
from nordscribe.services.model_service import ModelService

if TYPE_CHECKING:
    from nordscribe.ui.main_window import MainWindow

__all__ = ["build_window", "main"]

logger = logging.getLogger(__name__)


def build_window(settings: Settings) -> MainWindow:
    """Create the main window with production services."""
    # Imported lazily: ``nordscribe --version`` and unit tests should not load
    # CustomTkinter or Tk at all.
    from nordscribe.ui.main_window import MainWindow

    model_service = ModelService(ModelCache(WHISPER_CACHE_DIR))
    history = TranscriptionHistory()
    return MainWindow(settings, model_service=model_service, history=history)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the desktop application (or the diagnostic self-test)."""
    args = parse_args(argv)

    if args.version:
        # A windowed PyInstaller build has sys.stdout = None. Writing there
        # would raise and open a blocking Windows error dialog, so the version
        # is printed only when a console is attached.
        if sys.stdout is not None:
            sys.stdout.write(f"nordscribe {__version__}\n")
        return 0

    if args.selftest:
        from nordscribe.selftest import run_selftest

        return run_selftest(args.selftest_audio)

    log_path = setup_logging(console=not bool(getattr(sys, "frozen", False)))
    logger.info("NordScribe is starting (log: %s)", log_path)

    import customtkinter as ctk

    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")

    settings = Settings.load()
    logger.info(
        "settings: model=%s, language=%s, trim_silence=%s",
        settings.model,
        settings.language,
        settings.trim_silence,
    )

    app = build_window(settings)
    app.mainloop()
    return 0
