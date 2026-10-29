"""Tests for the static model catalog, labels and formatting."""

from __future__ import annotations

import unittest
from dataclasses import replace

from nordscribe.core.formatting import format_bytes
from nordscribe.core.models import (
    DEFAULT_MODEL_NAME,
    MODEL_NAMES,
    MODEL_SPECS,
    MODEL_SPECS_BY_NAME,
    aliases_for_filename,
    build_model_labels,
    clean_model_label,
    label_for_model,
)


class CatalogTest(unittest.TestCase):
    """Every model must have a usable URL, digest and file name."""

    def test_names_are_unique(self) -> None:
        self.assertEqual(len(MODEL_NAMES), len(set(MODEL_NAMES)))

    def test_every_spec_has_https_url_and_sha(self) -> None:
        for spec in MODEL_SPECS:
            with self.subTest(model=spec.name):
                self.assertTrue(spec.url.startswith("https://"))
                self.assertEqual(len(spec.sha256), 64)
                self.assertIn(spec.sha256, spec.url)
                self.assertTrue(spec.url.endswith("/" + spec.filename))
                self.assertTrue(spec.size_label)

    def test_large_maps_to_large_v3(self) -> None:
        self.assertEqual(MODEL_SPECS_BY_NAME["large"].filename, "large-v3.pt")

    def test_default_model_is_in_catalog(self) -> None:
        self.assertIn(DEFAULT_MODEL_NAME, MODEL_NAMES)

    def test_spec_rejects_digest_not_in_url(self) -> None:
        with self.assertRaises(ValueError):
            replace(MODEL_SPECS[0], sha256="0" * 64)

    def test_spec_rejects_wrong_filename(self) -> None:
        with self.assertRaises(ValueError):
            replace(MODEL_SPECS[0], filename="other.pt")

    def test_cache_aliases_include_large_variants(self) -> None:
        self.assertIn("large", aliases_for_filename("large-v3.pt"))
        self.assertIn("large-v2", aliases_for_filename("large-v2.pt"))
        self.assertIn("tiny", aliases_for_filename("tiny.pt"))
        self.assertEqual(aliases_for_filename("unknown.pt"), ())


class CleanModelLabelTest(unittest.TestCase):
    """Marker removal is what makes a label safe for Whisper."""

    def test_removes_current_markers(self) -> None:
        self.assertEqual(clean_model_label("🟢 tiny"), "tiny")
        self.assertEqual(clean_model_label("🔵 small"), "small")

    def test_tolerates_legacy_markers(self) -> None:
        self.assertEqual(clean_model_label("🟡 medium"), "medium")
        self.assertEqual(clean_model_label("⚪ large"), "large")

    def test_drops_trailing_explanations(self) -> None:
        self.assertEqual(clean_model_label("🟢 base (Доступна)"), "base")

    def test_passes_through_clean_names(self) -> None:
        self.assertEqual(clean_model_label("large-v3"), "large-v3")

    def test_handles_empty_input(self) -> None:
        self.assertEqual(clean_model_label(""), "")
        self.assertEqual(clean_model_label(None), "")
        self.assertEqual(clean_model_label("   "), "")


class BuildModelLabelsTest(unittest.TestCase):
    """Markers in the model dropdown."""

    def test_marks_downloaded_and_missing(self) -> None:
        result = build_model_labels(["tiny", "base"], {"tiny"})
        self.assertEqual(result, ["🟢 tiny", "🔵 base"])

    def test_marks_everything_missing_when_cache_empty(self) -> None:
        self.assertEqual(build_model_labels(["tiny"], set()), ["🔵 tiny"])

    def test_round_trip_through_cleaner(self) -> None:
        models = ["tiny", "base", "small"]
        built = build_model_labels(models, {"base"})
        self.assertEqual([clean_model_label(item) for item in built], models)


class LabelForModelTest(unittest.TestCase):
    """Canonical label lookup."""

    def test_finds_existing(self) -> None:
        options = ["🟢 tiny", "🔵 base"]
        self.assertEqual(label_for_model(options, "base"), "🔵 base")

    def test_returns_name_when_not_found(self) -> None:
        self.assertEqual(label_for_model(["🟢 tiny"], "huge"), "huge")


class FormatBytesTest(unittest.TestCase):
    """Human-readable sizes used by the model manager."""

    def test_scales(self) -> None:
        cases = {
            0: "0 Б",
            999: "999 Б",
            2048: "2 КБ",
            461234567: "440 МБ",
            3_100_000_000: "2.9 ГБ",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(format_bytes(value), expected)
