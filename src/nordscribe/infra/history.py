"""Transcription history stored in SQLite.

The database lives next to the settings in ``~/.nordscribe/history.db``. A new
connection is opened for every operation: ``sqlite3`` connections are not
thread-safe by default, and the app writes history from the UI thread while
another connection may be open elsewhere. Short-lived connections also avoid
keeping the file locked on Windows.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from nordscribe.infra.paths import APP_DIR

__all__ = [
    "DEFAULT_DB_PATH",
    "HISTORY_LIMIT",
    "HistoryEntry",
    "TranscriptionHistory",
    "format_local_time",
]

logger = logging.getLogger(__name__)

#: File in the application data directory.
DEFAULT_DB_PATH: Path = APP_DIR / "history.db"

#: Number of newest entries loaded when the history tab is opened.
HISTORY_LIMIT = 500

#: ``created_at`` is written by SQLite in UTC. ``id`` is the rowid, so
#: ``ORDER BY id DESC`` is deterministic even when several rows share the
#: same one-second timestamp. A separate index on ``id`` would duplicate the
#: primary-key index and is intentionally absent.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcription_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One stored transcription."""

    id: int
    text: str
    created_at: str


def format_local_time(created_at: str) -> str:
    """Convert SQLite's UTC timestamp to the local timezone for display.

    ``CURRENT_TIMESTAMP`` returns UTC; showing it unchanged would be off by
    the timezone offset. Invalid values are returned unchanged.
    """
    try:
        stamp = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return created_at or ""
    return stamp.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


class TranscriptionHistory:
    """SQLite-backed transcription history."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = Path(db_path or DEFAULT_DB_PATH)

    @property
    def path(self) -> Path:
        """Path to the SQLite file."""
        return self._path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection and always close it.

        ``with sqlite3.connect(...)`` manages the transaction only; it does
        not close the connection. The two responsibilities are separated here
        so the file is not left open until garbage collection.
        """
        connection = sqlite3.connect(self._path, timeout=5.0)
        try:
            with connection:  # transaction: commit / rollback
                yield connection
        finally:
            connection.close()

    def init_schema(self) -> bool:
        """Create the table when it does not exist.

        Returns:
            ``True`` on success; ``False`` when the directory or database is
            unavailable, in which case the app still runs without history.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(_SCHEMA)
        except (OSError, sqlite3.Error) as exc:
            logger.warning("history unavailable (%s): %s", self._path, exc)
            return False
        logger.info("history ready: %s", self._path)
        return True

    def add(self, text: str) -> HistoryEntry | None:
        """Insert a transcription.

        Returns:
            The stored entry with its generated id and timestamp, or ``None``
            when the text is empty or the database is unavailable.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO transcription_history (text) VALUES (?)",
                    (cleaned,),
                )
                row_id = int(cursor.lastrowid or 0)
                row = connection.execute(
                    "SELECT id, text, created_at FROM transcription_history WHERE id = ?",
                    (row_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            logger.warning("cannot save transcription: %s", exc)
            return None
        if row is None:
            return None
        return HistoryEntry(int(row[0]), str(row[1]), str(row[2]))

    def all(self, limit: int = HISTORY_LIMIT) -> list[HistoryEntry]:
        """Return newest entries first."""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT id, text, created_at FROM transcription_history "
                    "ORDER BY id DESC LIMIT ?",
                    (max(int(limit), 1),),
                ).fetchall()
        except sqlite3.Error as exc:
            logger.warning("cannot read history: %s", exc)
            return []
        return [HistoryEntry(int(row[0]), str(row[1]), str(row[2])) for row in rows]

    def count(self) -> int:
        """Return the number of stored entries (diagnostics and tests)."""
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT COUNT(*) FROM transcription_history").fetchone()
        except sqlite3.Error as exc:
            logger.warning("cannot count history: %s", exc)
            return 0
        return int(row[0]) if row else 0

    def clear(self) -> int:
        """Delete every entry and return the number of removed rows."""
        try:
            with self._connect() as connection:
                cursor = connection.execute("DELETE FROM transcription_history")
                return int(cursor.rowcount or 0)
        except sqlite3.Error as exc:
            logger.warning("cannot clear history: %s", exc)
            return 0
