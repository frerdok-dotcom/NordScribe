"""Tests for settings, validation, atomic writes and logging."""

from __future__ import annotations

import json
import logging
import unittest

from nordscribe.infra.settings import Settings, setup_logging
from tests import make_temp_dir, remove_temp_dir


class SettingsTest(unittest.TestCase):
    """Reading and writing user preferences."""

    def setUp(self) -> None:
        self._tmp = make_temp_dir()
        self.path = self._tmp / "settings.json"

    def tearDown(self) -> None:
        remove_temp_dir(self._tmp)

    def test_defaults_when_file_missing(self) -> None:
        config = Settings.load(self.path)
        self.assertEqual(config.model, "base")
        self.assertEqual(config.language, "ru")
        self.assertTrue(config.trim_silence)

    def test_round_trip(self) -> None:
        config = Settings(model="large", window_geometry="900x600")
        self.assertTrue(config.save(self.path))
        restored = Settings.load(self.path)
        self.assertEqual(restored.model, "large")
        self.assertEqual(restored.window_geometry, "900x600")

    def test_corrupt_file_falls_back_to_defaults(self) -> None:
        self.path.write_text("{ это не json", encoding="utf-8")
        self.assertEqual(Settings.load(self.path).model, "base")

    def test_unknown_fields_are_ignored(self) -> None:
        self.path.write_text(json.dumps({"model": "small", "мусор": 1}), encoding="utf-8")
        self.assertEqual(Settings.load(self.path).model, "small")

    def test_non_dict_json_falls_back(self) -> None:
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(Settings.load(self.path).model, "base")

    def test_wrong_value_types_are_ignored_per_field(self) -> None:
        self.path.write_text(
            json.dumps({"model": 5, "language": "en", "trim_silence": "yes"}),
            encoding="utf-8",
        )
        config = Settings.load(self.path)
        self.assertEqual(config.model, "base", "wrong model type falls back")
        self.assertEqual(config.language, "en", "valid fields survive")
        self.assertTrue(config.trim_silence, "wrong bool type falls back")

    def test_update_ignores_unknown_keys(self) -> None:
        config = Settings()
        config.update(model="medium", нет_такого_поля=42)
        self.assertEqual(config.model, "medium")
        self.assertFalse(hasattr(config, "нет_такого_поля"))

    def test_update_ignores_wrong_type(self) -> None:
        config = Settings()
        config.update(model=42)
        self.assertEqual(config.model, "base")

    def test_save_creates_missing_directories(self) -> None:
        nested = self._tmp / "a" / "b" / "settings.json"
        self.assertTrue(Settings().save(nested))
        self.assertTrue(nested.exists())

    def test_save_is_atomic_and_leaves_no_tmp(self) -> None:
        self.assertTrue(Settings().save(self.path))
        self.assertFalse(self.path.with_name("settings.json.tmp").exists())


class SetupLoggingTest(unittest.TestCase):
    """The log is written to a file and never prevents startup."""

    def setUp(self) -> None:
        self._tmp = make_temp_dir()
        self.path = self._tmp / "nested" / "app.log"
        self._root = logging.getLogger()
        self._saved_handlers = list(self._root.handlers)
        self._saved_level = self._root.level

    def tearDown(self) -> None:
        for handler in list(self._root.handlers):
            self._root.removeHandler(handler)
            handler.close()
        for handler in self._saved_handlers:
            self._root.addHandler(handler)
        self._root.setLevel(self._saved_level)
        remove_temp_dir(self._tmp)

    def test_writes_to_file(self) -> None:
        result = setup_logging(self.path, console=False)
        self.assertEqual(result, self.path)
        logging.getLogger("test").error("проверка журнала")
        for handler in logging.getLogger().handlers:
            handler.flush()
        self.assertIn("проверка журнала", self.path.read_text(encoding="utf-8"))

    def test_unwritable_path_returns_none(self) -> None:
        target = self._tmp / "dir"
        target.mkdir()
        self.assertIsNone(setup_logging(target, console=False))
