"""
Проверки окружения перед запуском.

Whisper требует ffmpeg для сжатых форматов (mp3, m4a), приложению нужен
доступный микрофон и права на запись в кэш моделей. Без этих проверок
пользователь получает невнятную ошибку уже во время работы, поэтому
окружение проверяется на старте и результат показывается в статусе.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import download_manager as dm

__all__ = ["CheckResult", "run_checks", "summarize"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckResult:
    """
    Результат одной проверки.

    Attributes:
        name: человекочитаемое название проверки.
        ok: прошла ли проверка.
        detail: пояснение для пользователя.
        fatal: мешает ли проблема работе приложения в целом. Проверки
            без этого флага лишь ограничивают часть возможностей: без
            ffmpeg нельзя открыть mp3, но WAV и микрофон работают.
    """

    name: str
    ok: bool
    detail: str
    fatal: bool = False


def _check_ffmpeg() -> CheckResult:
    """Ищет ffmpeg в PATH: без него Whisper не читает сжатые форматы."""
    found = shutil.which("ffmpeg")
    if found:
        return CheckResult("ffmpeg", True, found)
    return CheckResult(
        "ffmpeg",
        False,
        "не найден в PATH — доступны только WAV-файлы "
        "(установите ffmpeg и добавьте его в PATH)",
        fatal=False,
    )


def _check_cache(cache_dir: Path) -> CheckResult:
    """Проверяет, что в каталог моделей можно писать."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache_dir, prefix=".probe", delete=True):
            pass
    except OSError as exc:
        return CheckResult(
            "кэш моделей", False, f"{cache_dir} недоступен для записи: {exc}",
            fatal=True,
        )
    return CheckResult("кэш моделей", True, str(cache_dir), fatal=True)


def _check_audio_input() -> CheckResult:
    """Ищет устройство ввода звука по умолчанию."""
    try:
        import sounddevice as sd

        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001 — библиотека или драйвер могут отказать
        return CheckResult(
            "микрофон", False, f"не удалось опросить устройства: {exc}", fatal=False
        )

    inputs = [d for d in devices if d.get("max_input_channels", 0) > 0]
    if not inputs:
        return CheckResult(
            "микрофон", False,
            "устройства ввода не найдены — запись с микрофона недоступна",
            fatal=False,
        )
    try:
        default = sd.query_devices(kind="input")
        name = str(default.get("name", "по умолчанию"))
    except Exception:  # noqa: BLE001
        name = str(inputs[0].get("name", "по умолчанию"))
    return CheckResult("микрофон", True, name, fatal=False)


def run_checks(cache_dir: Optional[Path] = None) -> list[CheckResult]:
    """
    Выполняет все проверки окружения.

    Args:
        cache_dir: каталог кэша моделей; по умолчанию — каталог Whisper.

    Returns:
        Список результатов в порядке выполнения. Проверки не бросают
        исключений: любая внутренняя ошибка превращается в неуспешный
        результат, чтобы не мешать запуску приложения.
    """
    target = Path(cache_dir or dm.WHISPER_CACHE_DIR)
    results = [_check_cache(target), _check_ffmpeg(), _check_audio_input()]
    for result in results:
        if not result.ok:
            logger.warning("проверка «%s» не пройдена: %s", result.name, result.detail)
    return results


def summarize(results: list[CheckResult]) -> str:
    """
    Собирает короткое сообщение о проблемах для строки статуса.

    Returns:
        Пустую строку, если всё в порядке; иначе — перечисление проблем.
    """
    problems = [r for r in results if not r.ok]
    if not problems:
        return ""
    if len(problems) == 1:
        return f"{problems[0].name}: {problems[0].detail}"
    return f"проблемы окружения ({len(problems)}): " + "; ".join(
        p.name for p in problems
    )
