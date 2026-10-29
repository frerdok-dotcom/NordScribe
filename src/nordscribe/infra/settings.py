"""User settings and the rotating application log.

Settings are a small JSON file with forgiving validation: an unknown field, a
wrong type or a damaged file falls back to the default instead of preventing
startup. Writes are atomic (``.tmp`` + ``os.replace``), so a crash cannot leave
a half-written ``settings.json`` behind.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from contextlib import suppress
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from nordscribe.infra.paths import LOG_PATH, SETTINGS_PATH

__all__ = ["Settings", "setup_logging"]

logger = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_LOG_MAX_BYTES = 1024 * 1024
_LOG_BACKUPS = 3


@dataclass
class Settings:
    """User preferences stored between runs.

    All fields have safe defaults, so a missing or corrupt file never blocks
    startup.
    """

    model: str = "base"
    language: str = "ru"
    window_geometry: str = "1100x700"
    trim_silence: bool = True

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        """Read settings from disk, falling back to defaults on any problem."""
        path = Path(path or SETTINGS_PATH)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.debug("settings not read (%s), using defaults", exc)
            return cls()
        if not isinstance(raw, dict):
            logger.warning("settings file %s is not a JSON object, using defaults", path)
            return cls()
        return cls.from_mapping(raw)

    @classmethod
    def from_mapping(cls, raw: dict[str, object]) -> Settings:
        """Build settings from a JSON object, ignoring invalid values."""
        result = cls()
        for field in fields(cls):
            if field.name not in raw:
                continue
            value = raw[field.name]
            default = getattr(result, field.name)
            if isinstance(value, type(default)):
                setattr(result, field.name, value)
            else:
                logger.warning(
                    "settings: field %r has type %s, expected %s; keeping %r",
                    field.name,
                    type(value).__name__,
                    type(default).__name__,
                    default,
                )
        return result

    def save(self, path: Path | None = None) -> bool:
        """Write settings atomically.

        Returns:
            ``True`` on success. A write error is logged but not fatal.
        """
        path = Path(path or SETTINGS_PATH)
        temp = path.with_name(f"{path.name}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp.replace(path)
            return True
        except OSError as exc:
            logger.warning("settings not saved: %s", exc)
            with suppress(OSError):
                temp.unlink()
            return False

    def update(self, **values: Any) -> None:
        """Update known fields, ignoring unknown names and wrong types."""
        for field in fields(self):
            if field.name not in values:
                continue
            value = values[field.name]
            default = field.default
            if isinstance(value, type(default)):
                setattr(self, field.name, value)
            else:
                logger.warning(
                    "settings: ignoring %r=%r (expected %s)",
                    field.name,
                    value,
                    type(default).__name__,
                )


def setup_logging(
    path: Path | None = None,
    level: int = logging.INFO,
    console: bool = True,
) -> Path | None:
    """Configure the root logger with a rotating file and optional stderr.

    The application owns the root logger, so existing handlers are removed.
    If the log file cannot be opened the app continues with console output.

    Returns:
        The log path, or ``None`` when the file is unavailable.
    """
    log_path = Path(path or LOG_PATH)
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(_LOG_FORMAT)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUPS,
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("file log unavailable (%s), console only", exc)
        return None

    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    logger.info("log opened: %s (pid %d)", log_path, os.getpid())
    return log_path
