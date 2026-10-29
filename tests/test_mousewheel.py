"""Tests for the mouse-wheel helpers used by the history tab.

The pure event parsing and ancestry checks are tested here; the full scrolling
behaviour requires a live Tk loop and is covered by manual checks.
"""

from __future__ import annotations

import tkinter as tk
import unittest
from types import SimpleNamespace

from nordscribe.ui.mousewheel import destroyed, is_descendant, wheel_direction


def wheel_event(delta: int = 0, num: int = 0) -> SimpleNamespace:
    """Build an object with the fields a wheel event has."""
    return SimpleNamespace(delta=delta, num=num)


class WheelDirectionTest(unittest.TestCase):
    """Windows/macOS use delta; Linux/X11 uses virtual buttons."""

    def test_windows_delta_up_and_down(self) -> None:
        self.assertEqual(wheel_direction(wheel_event(delta=120)), -1)
        self.assertEqual(wheel_direction(wheel_event(delta=-120)), 1)

    def test_high_resolution_delta_is_one_step(self) -> None:
        self.assertEqual(wheel_direction(wheel_event(delta=1)), -1)
        self.assertEqual(wheel_direction(wheel_event(delta=-1)), 1)

    def test_linux_buttons(self) -> None:
        self.assertEqual(wheel_direction(wheel_event(num=4)), -1)
        self.assertEqual(wheel_direction(wheel_event(num=5)), 1)

    def test_event_without_wheel_data(self) -> None:
        self.assertEqual(wheel_direction(wheel_event()), 0)


class IsDescendantTest(unittest.TestCase):
    """The event source is decided by widget ancestry, not coordinates."""

    root: tk.Tk
    outer: tk.Frame
    inner: tk.Frame
    sibling: tk.Frame

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover - no display
            raise unittest.SkipTest(f"no graphical environment: {exc}") from exc
        cls.root.withdraw()
        cls.outer = tk.Frame(cls.root)
        cls.inner = tk.Frame(cls.outer)
        cls.sibling = tk.Frame(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def test_container_itself(self) -> None:
        self.assertTrue(is_descendant(self.outer, self.outer))

    def test_direct_and_nested_child(self) -> None:
        self.assertTrue(is_descendant(self.inner, self.outer))

    def test_sibling_is_not_descendant(self) -> None:
        self.assertFalse(is_descendant(self.sibling, self.outer))

    def test_root_is_not_descendant(self) -> None:
        self.assertFalse(is_descendant(self.root, self.outer))

    def test_none_is_not_descendant(self) -> None:
        self.assertFalse(is_descendant(None, self.outer))

    def test_destroyed_widget_is_not_descendant(self) -> None:
        doomed = tk.Frame(self.outer)
        doomed.destroy()
        self.assertFalse(is_descendant(doomed, self.outer))
        self.assertTrue(destroyed(doomed))

    def test_live_widget_is_not_destroyed(self) -> None:
        self.assertFalse(destroyed(self.inner))
