"""Tests for the SQLite transcription history."""

from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone

from nordscribe.infra.history import TranscriptionHistory, format_local_time
from tests import make_temp_dir, remove_temp_dir


class HistoryCase(unittest.TestCase):
    """A private database per test."""

    def setUp(self) -> None:
        self._tmp = make_temp_dir()
        self.db = self._tmp / "history.db"
        self.history = TranscriptionHistory(self.db)

    def tearDown(self) -> None:
        remove_temp_dir(self._tmp)


class SchemaTest(HistoryCase):
    """Table creation."""

    def test_init_creates_table(self) -> None:
        self.assertTrue(self.history.init_schema())
        with sqlite3.connect(self.db) as connection:
            names = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        self.assertIn("transcription_history", names)

    def test_init_is_idempotent(self) -> None:
        self.assertTrue(self.history.init_schema())
        self.assertTrue(self.history.init_schema())
        self.assertEqual(self.history.count(), 0)

    def test_schema_matches_specification(self) -> None:
        self.history.init_schema()
        with sqlite3.connect(self.db) as connection:
            columns = connection.execute("PRAGMA table_info(transcription_history)").fetchall()
        self.assertEqual([column[1] for column in columns], ["id", "text", "created_at"])
        self.assertEqual(columns[0][5], 1, "id must be the primary key")
        self.assertEqual(columns[1][3], 1, "text must be NOT NULL")

    def test_default_timestamp_is_set_by_database(self) -> None:
        self.history.init_schema()
        self.history.add("текст")
        with sqlite3.connect(self.db) as connection:
            row = connection.execute("SELECT created_at FROM transcription_history").fetchone()
        datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")


class AddTest(HistoryCase):
    """Inserting rows."""

    def test_add_returns_saved_entry(self) -> None:
        self.history.init_schema()
        entry = self.history.add("  привет мир  ")
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.text, "привет мир", "edges are stripped")
        self.assertGreater(entry.id, 0)
        self.assertTrue(entry.created_at)

    def test_empty_text_is_not_stored(self) -> None:
        self.history.init_schema()
        self.assertIsNone(self.history.add(""))
        self.assertIsNone(self.history.add("   \n  "))
        self.assertIsNone(self.history.add(None))  # type: ignore[arg-type]
        self.assertEqual(self.history.count(), 0)

    def test_ids_grow(self) -> None:
        self.history.init_schema()
        first = self.history.add("раз")
        second = self.history.add("два")
        assert first is not None and second is not None
        self.assertGreater(second.id, first.id)

    def test_unicode_and_multiline_text(self) -> None:
        self.history.init_schema()
        text = "Привет, мир!\nВторая строка — с тире и эмодзи 🎙️"
        self.history.add(text)
        self.assertEqual(self.history.all()[0].text, text)

    def test_very_long_text(self) -> None:
        self.history.init_schema()
        long_text = ("слово " * 5000).strip()
        self.assertIsNotNone(self.history.add(long_text))
        self.assertEqual(len(self.history.all()[0].text), len(long_text))

    def test_sql_injection_is_not_possible(self) -> None:
        self.history.init_schema()
        nasty = "'); DROP TABLE transcription_history; --"
        self.history.add(nasty)
        self.assertEqual(self.history.all()[0].text, nasty)
        self.assertEqual(self.history.count(), 1)

    def test_add_without_schema_returns_none(self) -> None:
        self.assertIsNone(self.history.add("текст"))


class ReadTest(HistoryCase):
    """Reading rows."""

    def test_all_returns_newest_first(self) -> None:
        self.history.init_schema()
        for text in ("первый", "второй", "третий"):
            self.history.add(text)
        self.assertEqual(
            [entry.text for entry in self.history.all()],
            ["третий", "второй", "первый"],
        )

    def test_all_on_empty_base(self) -> None:
        self.history.init_schema()
        self.assertEqual(self.history.all(), [])

    def test_all_respects_limit(self) -> None:
        self.history.init_schema()
        for index in range(10):
            self.history.add(f"запись {index}")
        self.assertEqual(
            [entry.text for entry in self.history.all(limit=3)],
            ["запись 9", "запись 8", "запись 7"],
        )

    def test_order_is_stable_when_timestamps_match(self) -> None:
        """
        CURRENT_TIMESTAMP has one-second resolution, so consecutive rows share
        a timestamp. Ordering by id keeps the order deterministic.
        """
        self.history.init_schema()
        for index in range(5):
            self.history.add(f"запись {index}")
        with sqlite3.connect(self.db) as connection:
            stamps = {
                row[0] for row in connection.execute("SELECT created_at FROM transcription_history")
            }
        self.assertEqual(len(stamps), 1, "the test is meaningful only with equal stamps")
        self.assertEqual(
            [entry.text for entry in self.history.all()],
            [f"запись {index}" for index in reversed(range(5))],
        )

    def test_all_is_newest_first_after_reopen(self) -> None:
        self.history.init_schema()
        self.history.add("старое")
        self.history.add("новое")
        reopened = TranscriptionHistory(self.db)
        reopened.init_schema()
        self.assertEqual([entry.text for entry in reopened.all()], ["новое", "старое"])


class ClearTest(HistoryCase):
    """Clearing the history."""

    def test_clear_removes_everything(self) -> None:
        self.history.init_schema()
        self.history.add("раз")
        self.history.add("два")
        self.assertEqual(self.history.clear(), 2)
        self.assertEqual(self.history.count(), 0)
        self.assertEqual(self.history.all(), [])

    def test_clear_on_empty_base(self) -> None:
        self.history.init_schema()
        self.assertEqual(self.history.clear(), 0)


class PersistenceTest(HistoryCase):
    """Data survives restarts and resources are released."""

    def test_survives_new_instance(self) -> None:
        self.history.init_schema()
        self.history.add("до перезапуска")
        reopened = TranscriptionHistory(self.db)
        reopened.init_schema()
        self.assertEqual([entry.text for entry in reopened.all()], ["до перезапуска"])

    def test_connection_is_really_closed(self) -> None:
        """
        ``with sqlite3.connect(...)`` closes the transaction, not the
        connection. On Windows an open handle prevents deleting the file.
        """
        self.history.init_schema()
        self.history.add("текст")
        self.history.all()
        self.db.unlink()
        self.assertFalse(self.db.exists())

    def test_parent_directory_is_created(self) -> None:
        nested = TranscriptionHistory(self._tmp / "a" / "b" / "history.db")
        self.assertTrue(nested.init_schema())
        self.assertTrue(nested.path.exists())

    def test_unavailable_database_degrades_gracefully(self) -> None:
        blocker = self._tmp / "файл-вместо-каталога"
        blocker.write_text("x", encoding="utf-8")
        broken = TranscriptionHistory(blocker / "history.db")
        self.assertFalse(broken.init_schema())
        self.assertIsNone(broken.add("текст"))
        self.assertEqual(broken.all(), [])
        self.assertEqual(broken.count(), 0)
        self.assertEqual(broken.clear(), 0)


class FormatLocalTimeTest(unittest.TestCase):
    """SQLite timestamps are UTC and must be shown in local time."""

    def test_converts_to_local_zone(self) -> None:
        stamp = "2026-10-04 14:19:20"
        expected = (
            datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=timezone.utc)
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S")
        )
        self.assertEqual(format_local_time(stamp), expected)

    def test_broken_value_is_returned_as_is(self) -> None:
        self.assertEqual(format_local_time("не дата"), "не дата")
        self.assertEqual(format_local_time(""), "")
        self.assertEqual(format_local_time(None), "")  # type: ignore[arg-type]

    def test_real_record_round_trip(self) -> None:
        tmp = make_temp_dir()
        try:
            store = TranscriptionHistory(tmp / "history.db")
            store.init_schema()
            entry = store.add("текст")
            assert entry is not None
            shown = datetime.strptime(
                format_local_time(entry.created_at),
                "%Y-%m-%d %H:%M:%S",
            )
            actual = (
                datetime.strptime(entry.created_at, "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=timezone.utc)
                .astimezone()
                .replace(tzinfo=None)
            )
            self.assertEqual(shown, actual)
        finally:
            remove_temp_dir(tmp)
