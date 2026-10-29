"""Environment checks run before the user starts working.

Missing ffmpeg only disables compressed formats, a missing microphone only
disables recording, but an unwritable model cache prevents the app from
downloading weights. The ``fatal`` flag lets the UI choose a red or orange
status message.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nordscribe.infra.paths import WHISPER_CACHE_DIR

__all__ = ["CheckResult", "has_fatal", "run_checks", "summarize"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Result of one environment check."""

    name: str
    ok: bool
    detail: str
    fatal: bool = False


def _check_ffmpeg() -> CheckResult:
    """Look for ffmpeg in PATH."""
    found = shutil.which("ffmpeg")
    if found:
        return CheckResult("ffmpeg", True, found)
    return CheckResult(
        "ffmpeg",
        False,
        "не найден в PATH — доступны только WAV-файлы (установите ffmpeg и добавьте его в PATH)",
        fatal=False,
    )


def _check_cache(cache_dir: Path) -> CheckResult:
    """Verify that the model cache is writable."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache_dir, prefix=".probe", delete=True):
            pass
    except OSError as exc:
        return CheckResult(
            "кэш моделей",
            False,
            f"{cache_dir} недоступен для записи: {exc}",
            fatal=True,
        )
    return CheckResult("кэш моделей", True, str(cache_dir), fatal=True)


def _check_audio_input() -> CheckResult:
    """Find a default audio input device (imports sounddevice lazily)."""
    try:
        import sounddevice as sd

        devices = sd.query_devices()
    except Exception as exc:
        return CheckResult(
            "микрофон",
            False,
            f"не удалось опросить устройства: {exc}",
            fatal=False,
        )

    inputs: list[dict[str, Any]] = [
        device for device in devices if device.get("max_input_channels", 0) > 0
    ]
    if not inputs:
        return CheckResult(
            "микрофон",
            False,
            "устройства ввода не найдены — запись с микрофона недоступна",
            fatal=False,
        )
    try:
        default = sd.query_devices(kind="input")
        name = str(default.get("name", "по умолчанию"))
    except Exception:
        name = str(inputs[0].get("name", "по умолчанию"))
    return CheckResult("микрофон", True, name, fatal=False)


def run_checks(cache_dir: Path | None = None) -> list[CheckResult]:
    """Run every check; a failing check never raises."""
    target = Path(cache_dir or WHISPER_CACHE_DIR)
    results = [_check_cache(target), _check_ffmpeg(), _check_audio_input()]
    for result in results:
        if not result.ok:
            logger.warning("check %r failed: %s", result.name, result.detail)
    return results


def summarize(results: list[CheckResult]) -> str:
    """Build a short status message with all failed checks."""
    problems = [result for result in results if not result.ok]
    if not problems:
        return ""
    if len(problems) == 1:
        return f"{problems[0].name}: {problems[0].detail}"
    return f"проблемы окружения ({len(problems)}): " + "; ".join(
        problem.name for problem in problems
    )


def has_fatal(results: list[CheckResult]) -> bool:
    """Whether any failed check prevents core functionality."""
    return any(not result.ok and result.fatal for result in results)
