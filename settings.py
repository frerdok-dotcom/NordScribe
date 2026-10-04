"""
Настройки и логирование NordScribe.

Всё, что приложение хранит между запусками, лежит в ``~/.nordscribe``:
``settings.json`` с пользовательскими предпочтениями и ``nordscribe.log``
с журналом работы. Раньше выбранная модель и размер окна терялись при
каждом перезапуске, а сообщения об ошибках уходили в ``print`` и исчезали.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Optional

__all__ = ["APP_DIR", "SETTINGS_PATH", "LOG_PATH", "Settings", "setup_logging"]

#: Каталог приложения в домашней папке пользователя.
APP_DIR: Path = Path.home() / ".nordscribe"
SETTINGS_PATH: Path = APP_DIR / "settings.json"
LOG_PATH: Path = APP_DIR / "nordscribe.log"

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_LOG_MAX_BYTES = 1024 * 1024
_LOG_BACKUPS = 3


@dataclass
class Settings:
    """
    Пользовательские предпочтения.

    Все поля имеют разумные значения по умолчанию, поэтому отсутствие или
    повреждение файла настроек не мешает запуску.
    """

    model: str = "base"
    language: str = "ru"
    window_geometry: str = "1100x700"
    trim_silence: bool = True

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Settings":
        """
        Читает настройки с диска.

        Неизвестные поля игнорируются, испорченный JSON не приводит к падению:
        в этом случае возвращаются значения по умолчанию.
        """
        path = Path(path or SETTINGS_PATH)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logging.getLogger(__name__).debug(
                "настройки не прочитаны (%s), беру значения по умолчанию", exc
            )
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Optional[Path] = None) -> bool:
        """
        Записывает настройки на диск.

        Returns:
            True при успехе. Ошибка записи не считается фатальной.
        """
        path = Path(path or SETTINGS_PATH)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except OSError as exc:
            logging.getLogger(__name__).warning("настройки не сохранены: %s", exc)
            return False

    def update(self, **values: Any) -> None:
        """Меняет перечисленные поля, игнорируя неизвестные имена."""
        known = {f.name for f in fields(self)}
        for key, value in values.items():
            if key in known:
                setattr(self, key, value)


def setup_logging(
    path: Optional[Path] = None,
    level: int = logging.INFO,
    console: bool = True,
) -> Optional[Path]:
    """
    Настраивает журнал в файл с ротацией.

    Args:
        path: путь к журналу; по умолчанию ``~/.nordscribe/nordscribe.log``.
        level: минимальный уровень записи.
        console: дублировать ли записи в stderr.

    Returns:
        Путь к журналу или ``None``, если файл недоступен для записи.
        Приложение в этом случае продолжает работать без файлового журнала.
    """
    path = Path(path or LOG_PATH)
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
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUPS,
            encoding="utf-8",
        )
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "файловый журнал недоступен (%s), пишу только в консоль", exc
        )
        return None

    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    logging.getLogger(__name__).info(
        "журнал открыт: %s (pid %d)", path, os.getpid()
    )
    return path
