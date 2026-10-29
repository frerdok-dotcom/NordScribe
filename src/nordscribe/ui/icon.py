"""Application icon loading.

The logo lives inside the package (``nordscribe/assets``) so it works both
from a source checkout and from the PyInstaller one-file bundle. On Windows
the window also gets the multi-size ``.ico`` and an explicit AppUserModelID,
otherwise the taskbar may group the app under the Python interpreter icon.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import tkinter as tk
from importlib import resources
from pathlib import Path

__all__ = ["APP_USER_MODEL_ID", "apply_window_icon", "asset_path", "install_windows_app_id"]

logger = logging.getLogger(__name__)

APP_USER_MODEL_ID = "frerdok.NordScribe"

_ICON_PNG = "logo_256.png"
_ICON_ICO = "logo.ico"


def asset_path(filename: str) -> Path | None:
    """Return a path to a packaged asset, or ``None`` when it is missing."""
    try:
        resource = resources.files("nordscribe").joinpath("assets").joinpath(filename)
        return Path(str(resource))
    except (ModuleNotFoundError, TypeError, ValueError) as exc:
        logger.warning("cannot resolve asset %r: %s", filename, exc)
        return None


def install_windows_app_id() -> None:
    """Give the process its own taskbar identity on Windows.

    Without this, a windowed Python app can be grouped under ``python.exe`` and
    show the Python icon in the taskbar even when the window icon is set.
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        logger.debug("could not set the Windows AppUserModelID", exc_info=True)


def apply_window_icon(window: tk.Tk | tk.Toplevel) -> None:
    """Set the window and taskbar icon from the packaged logo."""
    png = asset_path(_ICON_PNG)
    if png is not None and png.is_file():
        try:
            photo = tk.PhotoImage(master=window, file=str(png))
            window.iconphoto(True, photo)
            # Tk does not keep a Python reference to the image; without this
            # the icon may disappear after the local variable goes away.
            window._nordscribe_icon_photo = photo  # type: ignore[union-attr]
        except tk.TclError:
            logger.warning("could not set the PNG window icon", exc_info=True)

    ico = asset_path(_ICON_ICO)
    if sys.platform == "win32" and ico is not None and ico.is_file():
        try:
            # On Windows a multi-size .ico gives a crisp small title-bar icon
            # and a proper large taskbar icon.
            window.iconbitmap(default=str(ico))  # type: ignore[no-untyped-call]
        except tk.TclError:
            logger.warning("could not set the Windows icon", exc_info=True)
