"""Command-line arguments for the application and the self-test."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

__all__ = ["build_parser", "parse_args"]


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="nordscribe",
        description="Локальная транскрибация аудио через OpenAI Whisper.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="показать версию и выйти",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="проверить импорты, окно и окружение без ручных кликов",
    )
    parser.add_argument(
        "--selftest-audio",
        type=Path,
        metavar="PATH",
        help="WAV для реального распознавания во время --selftest",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse ``argv`` (``sys.argv[1:]`` by default)."""
    return build_parser().parse_args(list(argv) if argv is not None else sys.argv[1:])
