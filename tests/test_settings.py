"""Тесты настроек и настройки журнала."""

from __future__ import annotations

import json
import logging
import unittest

import settings as app_settings
from tests import make_temp_dir, remove_temp_dir


class SettingsTest(unittest.TestCase):
    """Чтение и запись пользовательских предпочтений."""

    def setUp(self) -> None:
        self._tmp = make_temp_dir()
        self.path = self._tmp / "settings.json"

    def tearDown(self) -> None:
        remove_temp_dir(self._tmp)

    def test_defaults_when_file_missing(self) -> None:
        config = app_settings.Settings.load(self.path)
        self.assertEqual(config.model, "base")
        self.assertEqual(config.language, "ru")
        self.assertTrue(config.trim_silence)

    def test_round_trip(self) -> None:
        config = app_settings.Settings(model="large", window_geometry="900x600")
        self.assertTrue(config.save(self.path))
        restored = app_settings.Settings.load(self.path)
        self.assertEqual(restored.model, "large")
        self.assertEqual(restored.window_geometry, "900x600")

    def test_corrupt_file_falls_back_to_defaults(self) -> None:
        self.path.write_text("{ это не json", encoding="utf-8")
        config = app_settings.Settings.load(self.path)
        self.assertEqual(config.model, "base")

    def test_unknown_fields_are_ignored(self) -> None:
        self.path.write_text(json.dumps({"model": "small", "мусор": 1}), encoding="utf-8")
        config = app_settings.Settings.load(self.path)
        self.assertEqual(config.model, "small")

    def test_non_dict_json_falls_back(self) -> None:
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(app_settings.Settings.load(self.path).model, "base")

    def test_update_ignores_unknown_keys(self) -> None:
        config = app_settings.Settings()
        config.update(model="medium", нет_такого_поля=42)
        self.assertEqual(config.model, "medium")
        self.assertFalse(hasattr(config, "нет_такого_поля"))

    def test_save_creates_missing_directories(self) -> None:
        nested = self._tmp / "a" / "b" / "settings.json"
        self.assertTrue(app_settings.Settings().save(nested))
        self.assertTrue(nested.exists())


class SetupLoggingTest(unittest.TestCase):
    """Журнал пишется в файл и не ломает приложение при недоступном пути."""

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
        result = app_settings.setup_logging(self.path, console=False)
        self.assertEqual(result, self.path)
        logging.getLogger("test").error("проверка журнала")
        for handler in logging.getLogger().handlers:
            handler.flush()
        self.assertIn("проверка журнала", self.path.read_text(encoding="utf-8"))

    def test_unwritable_path_returns_none(self) -> None:
        # Каталог вместо файла: открыть журнал невозможно.
        target = self._tmp / "dir"
        target.mkdir()
        result = app_settings.setup_logging(target, console=False)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
