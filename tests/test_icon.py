"""Tests for the packaged logo and window icon."""

from __future__ import annotations

import struct
import tkinter as tk
import unittest

from nordscribe.ui.icon import apply_window_icon, asset_path, install_windows_app_id


class AssetTest(unittest.TestCase):
    """The icon files ship with the package."""

    def test_logo_files_exist(self) -> None:
        for name in ("logo.png", "logo_256.png", "logo.ico"):
            with self.subTest(name=name):
                path = asset_path(name)
                self.assertIsNotNone(path)
                assert path is not None
                self.assertTrue(path.is_file(), path)

    def test_png_is_square_256(self) -> None:
        path = asset_path("logo_256.png")
        assert path is not None
        data = path.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual((width, height), (256, 256))

    def test_ico_contains_multiple_sizes(self) -> None:
        path = asset_path("logo.ico")
        assert path is not None
        data = path.read_bytes()
        count = int.from_bytes(data[4:6], "little")
        self.assertGreaterEqual(count, 5, "the ICO should include small and large sizes")


class WindowIconTest(unittest.TestCase):
    """Tk accepts the packaged PNG/ICO; skipped without a display."""

    def test_apply_to_tk_root(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover - no display
            raise unittest.SkipTest(f"no graphical environment: {exc}") from exc
        root.withdraw()
        try:
            apply_window_icon(root)
            self.assertTrue(hasattr(root, "_nordscribe_icon_photo"))
        finally:
            root.destroy()

    def test_app_id_is_safe_to_call(self) -> None:
        install_windows_app_id()
