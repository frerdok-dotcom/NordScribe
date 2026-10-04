"""Тесты чистых функций подписей моделей (без GUI)."""

from __future__ import annotations

import unittest

import model_labels as labels


class CleanModelLabelTest(unittest.TestCase):
    """Очистка подписи от маркера — то, что уходит в whisper.load_model()."""

    def test_removes_current_markers(self) -> None:
        self.assertEqual(labels.clean_model_label("🟢 tiny"), "tiny")
        self.assertEqual(labels.clean_model_label("🔵 small"), "small")

    def test_tolerates_legacy_markers(self) -> None:
        self.assertEqual(labels.clean_model_label("🟡 medium"), "medium")
        self.assertEqual(labels.clean_model_label("⚪ large"), "large")

    def test_drops_trailing_explanations(self) -> None:
        self.assertEqual(labels.clean_model_label("🟢 base (Доступна)"), "base")

    def test_passes_through_clean_names(self) -> None:
        self.assertEqual(labels.clean_model_label("large-v3"), "large-v3")

    def test_handles_empty_input(self) -> None:
        self.assertEqual(labels.clean_model_label(""), "")
        self.assertEqual(labels.clean_model_label(None), "")
        self.assertEqual(labels.clean_model_label("   "), "")


class BuildModelLabelsTest(unittest.TestCase):
    """Маркеры статуса в выпадающем списке."""

    def test_marks_downloaded_and_missing(self) -> None:
        result = labels.build_model_labels(["tiny", "base"], {"tiny"})
        self.assertEqual(result, ["🟢 tiny", "🔵 base"])

    def test_marks_everything_missing_when_cache_empty(self) -> None:
        result = labels.build_model_labels(["tiny"], set())
        self.assertEqual(result, ["🔵 tiny"])

    def test_round_trip_through_cleaner(self) -> None:
        models = ["tiny", "base", "small"]
        built = labels.build_model_labels(models, {"base"})
        self.assertEqual([labels.clean_model_label(x) for x in built], models)


class LabelForModelTest(unittest.TestCase):
    """Поиск канонической подписи для модели."""

    def test_finds_existing(self) -> None:
        options = ["🟢 tiny", "🔵 base"]
        self.assertEqual(labels.label_for_model(options, "base"), "🔵 base")

    def test_returns_name_when_not_found(self) -> None:
        self.assertEqual(labels.label_for_model(["🟢 tiny"], "huge"), "huge")


if __name__ == "__main__":
    unittest.main()
