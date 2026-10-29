"""Headless diagnostic run of the packaged application.

``nordscribe --selftest`` creates the real window, runs the environment checks
and (optionally) transcribes a WAV file with the smallest downloaded model.
Return codes: 0 success, 1 failure, 2 nothing to transcribe.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from nordscribe.app import build_window
from nordscribe.core.models import MODEL_NAMES
from nordscribe.infra.cache import ModelCache
from nordscribe.infra.paths import WHISPER_CACHE_DIR
from nordscribe.infra.settings import Settings, setup_logging
from nordscribe.services.model_service import ModelService
from nordscribe.services.preflight import run_checks
from nordscribe.services.transcriber import Transcriber

__all__ = ["run_selftest"]

logger = logging.getLogger(__name__)


def _smallest_downloaded_model(service: ModelService) -> str | None:
    """Pick the first cached model in menu order without downloading."""
    available = service.downloaded()
    for name in MODEL_NAMES:
        if name in available:
            return name
    return None


def run_selftest(audio_path: Path | None) -> int:
    """Run the diagnostic scenario and return a process exit code."""
    log_path = setup_logging(console=not bool(getattr(sys, "frozen", False)))
    logger.info(
        "SELFTEST: start (frozen=%s, exe=%s, _MEIPASS=%s, log=%s)",
        bool(getattr(sys, "frozen", False)),
        sys.executable,
        getattr(sys, "_MEIPASS", "-"),
        log_path,
    )

    try:
        results = run_checks()
        for result in results:
            logger.info(
                "SELFTEST: check %r: %s — %s",
                result.name,
                "ok" if result.ok else "no",
                result.detail,
            )

        import customtkinter as ctk

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        settings = Settings.load()
        app = build_window(settings)
        app.update_idletasks()
        app.update()
        logger.info("SELFTEST: main window built (%s)", app.geometry())
        app.destroy()
        logger.info("SELFTEST: main window closed")

        if audio_path is None:
            logger.info("SELFTEST: OK (transcription skipped: no WAV)")
            return 0
        if not audio_path.is_file():
            logger.error("SELFTEST: file not found: %s", audio_path)
            return 2

        service = ModelService(ModelCache(WHISPER_CACHE_DIR))
        model_name = _smallest_downloaded_model(service)
        if model_name is None:
            logger.warning("SELFTEST: no downloaded models — transcription skipped")
            return 2

        transcriber = Transcriber(
            model_name_provider=lambda: model_name,
            language="en",
            verify_cache=False,
        )
        try:
            text = transcriber.transcribe(
                audio_path,
                on_status=lambda message: logger.info("SELFTEST: %s", message),
            )
        finally:
            transcriber.shutdown(wait=True)

        logger.info(
            "SELFTEST: model %s, characters %d: %r",
            model_name,
            len(text),
            text[:200],
        )
        if not text.strip():
            logger.error("SELFTEST: no speech recognised")
            return 1
        logger.info("SELFTEST: OK")
        return 0
    except Exception:
        logger.exception("SELFTEST: FAILED")
        return 1
