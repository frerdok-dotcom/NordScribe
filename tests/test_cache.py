"""Tests for the Whisper model cache."""

from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from pathlib import Path

from nordscribe.core.models import MODEL_SPECS_BY_NAME
from nordscribe.infra.cache import ModelCache
from tests import make_temp_dir, remove_temp_dir

PAYLOAD = b"model-bytes"


class CacheCase(unittest.TestCase):
    """A fresh cache directory per test."""

    def setUp(self) -> None:
        self._tmp = make_temp_dir()
        self.cache = ModelCache(self._tmp)

    def tearDown(self) -> None:
        remove_temp_dir(self._tmp)


class ScanTest(CacheCase):
    """Scanning recognises aliases and ignores unrelated files."""

    def test_large_v3_also_satisfies_large(self) -> None:
        (self._tmp / "large-v3.pt").write_bytes(b"x")
        found = self.cache.downloaded()
        self.assertIn("large-v3", found)
        self.assertIn("large", found)

    def test_ignores_unrelated_files(self) -> None:
        (self._tmp / "случайный.pt").write_bytes(b"x")
        (self._tmp / "notes.txt").write_text("x", encoding="utf-8")
        self.assertEqual(self.cache.downloaded(), set())

    def test_missing_directory_is_empty(self) -> None:
        self.assertEqual(ModelCache(self._tmp / "нет-такого").downloaded(), set())

    def test_size_of(self) -> None:
        spec = MODEL_SPECS_BY_NAME["tiny"]
        self.assertIsNone(self.cache.size_of(spec))
        path = self.cache.file_path(spec)
        path.write_bytes(PAYLOAD)
        self.assertEqual(self.cache.size_of(spec), len(PAYLOAD))


class IntegrityTest(CacheCase):
    """SHA-256 verification and removal."""

    def test_missing_file_is_none(self) -> None:
        self.assertIsNone(self.cache.is_valid(MODEL_SPECS_BY_NAME["tiny"]))

    def test_matching_hash_is_true(self) -> None:
        digest = hashlib.sha256(PAYLOAD).hexdigest()
        spec = replace(
            MODEL_SPECS_BY_NAME["tiny"],
            filename="tiny.pt",
            url=f"https://example.invalid/{digest}/tiny.pt",
            sha256=digest,
        )
        self.cache.file_path(spec).write_bytes(PAYLOAD)
        self.assertTrue(self.cache.is_valid(spec))

    def test_mismatched_hash_is_false(self) -> None:
        spec = MODEL_SPECS_BY_NAME["tiny"]
        self.cache.file_path(spec).write_bytes(b"truncated-weights")
        self.assertFalse(self.cache.is_valid(spec))

    def test_remove_deletes_weights_and_part(self) -> None:
        spec = MODEL_SPECS_BY_NAME["tiny"]
        self.cache.file_path(spec).write_bytes(b"x")
        self.cache.part_path(spec).write_bytes(b"y")
        removed = self.cache.remove(spec)
        self.assertEqual(
            sorted(item.path.name for item in removed),
            ["tiny.pt", "tiny.pt.part"],
        )
        self.assertEqual(sum(item.size for item in removed), 2)
        self.assertFalse(self.cache.file_path(spec).exists())

    def test_remove_on_empty_cache_is_noop(self) -> None:
        self.assertEqual(self.cache.remove(MODEL_SPECS_BY_NAME["tiny"]), [])

    def test_part_path_is_next_to_weights(self) -> None:
        spec = MODEL_SPECS_BY_NAME["large"]
        self.assertEqual(self.cache.part_path(spec).name, "large-v3.pt.part")
        self.assertIsInstance(self.cache.file_path(spec), Path)
