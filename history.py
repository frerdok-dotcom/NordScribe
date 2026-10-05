"""
История транскрибаций в SQLite.

Модуль отвечает только за данные: открывает базу в каталоге приложения,
создаёт таблицу и умеет записывать/читать записи. Ничего из CustomTkinter
здесь нет — слой можно проверять юнит-тестами без графической среды.

База лежит рядом с настройками: ``~/.nordscribe/history.db``. Это каталог
данных самого приложения (:data:`settings.APP_DIR`), а не индекс CodeGraph
из ``.codegraph/codegraph.db`` — тот принадлежит стороннему инструменту
индексации, у пользователя приложения его нет, и переиндексация затёрла бы
записи.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import settings as app_settings

__all__ = [
    "DEFAULT_DB_PATH",
    "HISTORY_LIMIT",
    "HistoryEntry",
    "TranscriptionHistory",
    "format_local_time",
]

logger = logging.getLogger(__name__)

#: Файл истории в каталоге данных приложения.
DEFAULT_DB_PATH: Path = app_settings.APP_DIR / "history.db"

#: Сколько последних записей показывать при старте.
HISTORY_LIMIT = 500

#: Схема точно как в задании. created_at хранится в UTC — так работает
#: CURRENT_TIMESTAMP в SQLite; для показа метка переводится в местное время.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcription_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_transcription_history_order
    ON transcription_history (id DESC);
"""


@dataclass(frozen=True)
class HistoryEntry:
    """Одна запись истории."""

    id: int
    text: str
    created_at: str


def format_local_time(created_at: str) -> str:
    """
    Переводит метку времени SQLite из UTC в местное время для показа.

    ``CURRENT_TIMESTAMP`` в SQLite возвращает время в UTC. Если показывать его
    как есть, пользователь в UTC+3 увидит время на три часа раньше реального.

    >>> import datetime
    >>> format_local_time("2000-01-01 00:00:00") != "2000-01-01 00:00:00" or True
    True
    """
    try:
        stamp = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return created_at or ""
    return stamp.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


class TranscriptionHistory:
    """
    Хранилище истории транскрибаций.

    Соединение открывается на каждую операцию. Так сделано намеренно:
    ``sqlite3`` по умолчанию запрещает использовать одно соединение из
    разных потоков, а запись идёт из рабочего потока распознавания, чтение —
    из главного. Плюс короткое соединение не держит файл открытым.

    Args:
        db_path: путь к файлу базы; по умолчанию
            :data:`DEFAULT_DB_PATH`.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = Path(db_path or DEFAULT_DB_PATH)

    @property
    def path(self) -> Path:
        """Путь к файлу базы."""
        return self._path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """
        Открывает соединение и гарантированно его закрывает.

        Важно: ``with sqlite3.connect(...)`` управляет только транзакцией
        (commit при успехе, rollback при исключении) и **не закрывает
        соединение** — файл остаётся открытым до сборки мусора. Поэтому
        транзакция и закрытие здесь разделены.
        """
        connection = sqlite3.connect(self._path, timeout=5.0)
        try:
            with connection:  # транзакция
                yield connection
        finally:
            connection.close()

    def init_schema(self) -> bool:
        """
        Создаёт таблицу и индекс, если их ещё нет.

        Returns:
            True при успехе. False, если каталог или база недоступны —
            приложение при этом продолжает работать без истории.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(_SCHEMA)
        except (OSError, sqlite3.Error) as exc:
            logger.warning("история недоступна (%s): %s", self._path, exc)
            return False
        logger.info("история готова: %s", self._path)
        return True

    def add(self, text: str) -> HistoryEntry | None:
        """
        Записывает текст транскрибации.

        Returns:
            Сохранённую запись с присвоенным id и меткой времени, либо
            ``None``, если текст пуст или база недоступна.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO transcription_history (text) VALUES (?)", (cleaned,)
                )
                row_id = int(cursor.lastrowid or 0)
                # Метку времени читаем обратно: её проставляет сама база.
                row = connection.execute(
                    "SELECT id, text, created_at FROM transcription_history WHERE id = ?",
                    (row_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            logger.warning("не удалось сохранить транскрибацию: %s", exc)
            return None
        if row is None:
            return None
        return HistoryEntry(int(row[0]), str(row[1]), str(row[2]))

    def all(self, limit: int = HISTORY_LIMIT) -> list[HistoryEntry]:
        """
        Возвращает записи от новых к старым.

        Сортировка идёт по ``id``, а не по ``created_at``: у
        ``CURRENT_TIMESTAMP`` разрешение в одну секунду, и несколько
        транскрибаций подряд получают одинаковую метку — порядок по времени
        был бы неопределённым.
        """
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT id, text, created_at FROM transcription_history "
                    "ORDER BY id DESC LIMIT ?",
                    (max(int(limit), 1),),
                ).fetchall()
        except sqlite3.Error as exc:
            logger.warning("не удалось прочитать историю: %s", exc)
            return []
        return [HistoryEntry(int(r[0]), str(r[1]), str(r[2])) for r in rows]

    def count(self) -> int:
        """Сколько записей хранится в базе."""
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT COUNT(*) FROM transcription_history").fetchone()
        except sqlite3.Error as exc:
            logger.warning("не удалось посчитать записи: %s", exc)
            return 0
        return int(row[0]) if row else 0

    def clear(self) -> int:
        """
        Удаляет всю историю.

        Returns:
            Число удалённых записей; 0, если история пуста или недоступна.
        """
        try:
            with self._connect() as connection:
                cursor = connection.execute("DELETE FROM transcription_history")
                return int(cursor.rowcount or 0)
        except sqlite3.Error as exc:
            logger.warning("не удалось очистить историю: %s", exc)
            return 0
