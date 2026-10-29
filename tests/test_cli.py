"""Tests for command-line parsing and the fastest entry point."""

from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path

from nordscribe.app import main
from nordscribe.cli import parse_args


class ParseArgsTest(unittest.TestCase):
    """Defaults and options are stable for scripts and the self-test."""

    def test_defaults(self) -> None:
        args = parse_args([])
        self.assertFalse(args.selftest)
        self.assertFalse(args.version)
        self.assertIsNone(args.selftest_audio)

    def test_selftest_options(self) -> None:
        args = parse_args(["--selftest", "--selftest-audio", "sample.wav"])
        self.assertTrue(args.selftest)
        self.assertEqual(args.selftest_audio, Path("sample.wav"))

    def test_version_is_a_flag(self) -> None:
        # A flag (not argparse's action="version") lets the windowed build stay
        # silent instead of raising on a missing sys.stdout.
        self.assertTrue(parse_args(["--version"]).version)


class VersionTest(unittest.TestCase):
    """``--version`` must not import the GUI or heavy dependencies."""

    def test_prints_version_and_returns_zero(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("nordscribe", output.getvalue().lower())
