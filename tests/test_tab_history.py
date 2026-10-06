"""Тесты механики колеса мыши во вкладке «История».

Проверяются только чистые части механики: разбор события колеса и
определение «событие пришло со списка истории». Полная прокрутка требует
живого цикла Tk, поэтому здесь её нет — эти функции были источником
регрессий («колесо над карточкой не прокручивает список», «колесо над
кнопкой очистки прокручивает список»).
"""

from __future__ import annotations

import tkinter as tk
import unittest
from types import SimpleNamespace

import tab_history


def wheel_event(delta: int = 0, num: int = 0) -> SimpleNamespace:
    """Событие колеса с нужными полями (реальное ``tk.Event`` не создать)."""
    return SimpleNamespace(delta=delta, num=num)


class WheelDirectionTest(unittest.TestCase):
    """Разбор события колеса: Windows/macOS — delta, Linux/X11 — num."""

    def test_windows_delta_up_and_down(self) -> None:
        self.assertEqual(tab_history._wheel_direction(wheel_event(delta=120)), -1)
        self.assertEqual(tab_history._wheel_direction(wheel_event(delta=-120)), 1)

    def test_high_resolution_delta_is_one_step(self) -> None:
        # Тачпады и «быстрые» колёса присылают маленькие дельты: направление
        # важнее величины, иначе список прокручивался бы «через край»
        # (маленькая дельта — всё равно одно движение колеса).
        self.assertEqual(tab_history._wheel_direction(wheel_event(delta=1)), -1)
        self.assertEqual(tab_history._wheel_direction(wheel_event(delta=-1)), 1)

    def test_linux_buttons(self) -> None:
        self.assertEqual(tab_history._wheel_direction(wheel_event(num=4)), -1)
        self.assertEqual(tab_history._wheel_direction(wheel_event(num=5)), 1)

    def test_event_without_wheel_data(self) -> None:
        self.assertEqual(tab_history._wheel_direction(wheel_event()), 0)


class IsDescendantTest(unittest.TestCase):
    """«Событие пришло со списка» определяется по дереву виджетов."""

    root: tk.Tk
    outer: tk.Frame
    inner: tk.Frame
    sibling: tk.Frame

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover — нет графической среды
            raise unittest.SkipTest(f"нет графической среды: {exc}") from exc
        cls.root.withdraw()
        cls.outer = tk.Frame(cls.root)
        cls.inner = tk.Frame(cls.outer)
        cls.sibling = tk.Frame(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def test_container_itself(self) -> None:
        self.assertTrue(tab_history._is_descendant(self.outer, self.outer))

    def test_direct_and_nested_child(self) -> None:
        self.assertTrue(tab_history._is_descendant(self.inner, self.outer))

    def test_sibling_is_not_descendant(self) -> None:
        # Кнопка «Очистить историю» живёт рядом со списком: её колесо не
        # должно прокручивать историю.
        self.assertFalse(tab_history._is_descendant(self.sibling, self.outer))

    def test_root_is_not_descendant(self) -> None:
        self.assertFalse(tab_history._is_descendant(self.root, self.outer))

    def test_none_is_not_descendant(self) -> None:
        self.assertFalse(tab_history._is_descendant(None, self.outer))

    def test_destroyed_widget_is_not_descendant(self) -> None:
        doomed = tk.Frame(self.outer)
        doomed.destroy()
        self.assertFalse(tab_history._is_descendant(doomed, self.outer))
        self.assertTrue(tab_history._destroyed(doomed))

    def test_live_widget_is_not_destroyed(self) -> None:
        self.assertFalse(tab_history._destroyed(self.inner))


if __name__ == "__main__":
    unittest.main()
