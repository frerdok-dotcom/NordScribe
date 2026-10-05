"""Тесты проверок окружения."""

from __future__ import annotations

import dataclasses
import shutil
import unittest

import preflight
from tests import make_temp_dir, remove_temp_dir


class CheckResultTest(unittest.TestCase):
    """Модель результата проверки."""

    def test_defaults_to_non_fatal(self) -> None:
        result = preflight.CheckResult("тест", True, "ок")
        self.assertFalse(result.fatal)

    def test_is_immutable(self) -> None:
        result = preflight.CheckResult("тест", True, "ок")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.ok = False  # type: ignore[misc]


class SummarizeTest(unittest.TestCase):
    """Сводка проблем для строки статуса."""

    def test_empty_when_all_ok(self) -> None:
        results = [preflight.CheckResult("a", True, "ок")]
        self.assertEqual(preflight.summarize(results), "")

    def test_single_problem_is_detailed(self) -> None:
        results = [preflight.CheckResult("ffmpeg", False, "не найден")]
        self.assertEqual(preflight.summarize(results), "ffmpeg: не найден")

    def test_many_problems_are_counted(self) -> None:
        results = [
            preflight.CheckResult("a", False, "1"),
            preflight.CheckResult("b", False, "2"),
        ]
        summary = preflight.summarize(results)
        self.assertIn("2", summary)
        self.assertIn("a", summary)
        self.assertIn("b", summary)


class RunChecksTest(unittest.TestCase):
    """Сами проверки не должны падать и не должны мешать запуску."""

    def setUp(self) -> None:
        self._tmp = make_temp_dir()
        self.cache = self._tmp / "models"

    def tearDown(self) -> None:
        remove_temp_dir(self._tmp)

    def test_writable_cache_passes(self) -> None:
        results = preflight.run_checks(self.cache)
        cache = next(r for r in results if r.name == "кэш моделей")
        self.assertTrue(cache.ok)
        self.assertTrue(self.cache.exists())

    def test_unwritable_cache_is_fatal(self) -> None:
        blocker = self._tmp / "файл-вместо-каталога"
        blocker.write_text("x", encoding="utf-8")
        results = preflight.run_checks(blocker)
        cache = next(r for r in results if r.name == "кэш моделей")
        self.assertFalse(cache.ok)
        self.assertTrue(cache.fatal)

    def test_all_checks_present(self) -> None:
        names = {r.name for r in preflight.run_checks(self.cache)}
        self.assertEqual(names, {"кэш моделей", "ffmpeg", "микрофон"})

    def test_ffmpeg_check_matches_path(self) -> None:
        results = preflight.run_checks(self.cache)
        ffmpeg = next(r for r in results if r.name == "ffmpeg")
        self.assertEqual(ffmpeg.ok, shutil.which("ffmpeg") is not None)

    def test_run_checks_never_raises(self) -> None:
        # Каталог внутри несуществующего корня: проверка должна вернуть
        # результат, а не исключение.
        weird = self._tmp / "a" / "b" / "c"
        self.assertIsInstance(preflight.run_checks(weird), list)


if __name__ == "__main__":
    unittest.main()
