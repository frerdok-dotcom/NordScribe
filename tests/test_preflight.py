"""Tests for environment checks."""

from __future__ import annotations

import dataclasses
import shutil
import unittest

from nordscribe.services.preflight import (
    CheckResult,
    has_fatal,
    run_checks,
    summarize,
)
from tests import make_temp_dir, remove_temp_dir


class CheckResultTest(unittest.TestCase):
    """The result model."""

    def test_defaults_to_non_fatal(self) -> None:
        result = CheckResult("тест", True, "ок")
        self.assertFalse(result.fatal)

    def test_is_immutable(self) -> None:
        result = CheckResult("тест", True, "ок")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.ok = False  # type: ignore[misc]


class SummarizeTest(unittest.TestCase):
    """Short status messages."""

    def test_empty_when_all_ok(self) -> None:
        self.assertEqual(summarize([CheckResult("a", True, "ок")]), "")

    def test_single_problem_is_detailed(self) -> None:
        results = [CheckResult("ffmpeg", False, "не найден")]
        self.assertEqual(summarize(results), "ffmpeg: не найден")

    def test_many_problems_are_counted(self) -> None:
        results = [
            CheckResult("a", False, "1"),
            CheckResult("b", False, "2"),
        ]
        summary = summarize(results)
        self.assertIn("2", summary)
        self.assertIn("a", summary)
        self.assertIn("b", summary)

    def test_has_fatal(self) -> None:
        self.assertFalse(has_fatal([CheckResult("a", False, "x")]))
        self.assertTrue(has_fatal([CheckResult("a", False, "x", fatal=True)]))
        self.assertFalse(has_fatal([CheckResult("a", True, "x", fatal=True)]))


class RunChecksTest(unittest.TestCase):
    """Checks never raise and never block startup."""

    def setUp(self) -> None:
        self._tmp = make_temp_dir()
        self.cache = self._tmp / "models"

    def tearDown(self) -> None:
        remove_temp_dir(self._tmp)

    def test_writable_cache_passes(self) -> None:
        results = run_checks(self.cache)
        cache = next(result for result in results if result.name == "кэш моделей")
        self.assertTrue(cache.ok)
        self.assertTrue(self.cache.exists())

    def test_unwritable_cache_is_fatal(self) -> None:
        blocker = self._tmp / "файл-вместо-каталога"
        blocker.write_text("x", encoding="utf-8")
        results = run_checks(blocker)
        cache = next(result for result in results if result.name == "кэш моделей")
        self.assertFalse(cache.ok)
        self.assertTrue(cache.fatal)

    def test_all_checks_present(self) -> None:
        names = {result.name for result in run_checks(self.cache)}
        self.assertEqual(names, {"кэш моделей", "ffmpeg", "микрофон"})

    def test_ffmpeg_check_matches_path(self) -> None:
        ffmpeg = next(result for result in run_checks(self.cache) if result.name == "ffmpeg")
        self.assertEqual(ffmpeg.ok, shutil.which("ffmpeg") is not None)

    def test_run_checks_never_raises(self) -> None:
        weird = self._tmp / "a" / "b" / "c"
        self.assertIsInstance(run_checks(weird), list)
