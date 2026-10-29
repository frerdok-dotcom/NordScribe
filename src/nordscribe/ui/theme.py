"""Colours, fonts and small UI helpers.

The application always runs in the dark theme. Colours are defined once here
instead of being repeated in tabs and dialogs.
"""

from __future__ import annotations

import logging

import customtkinter as ctk

__all__ = [
    "COLOR_ACCENT",
    "COLOR_BLUE",
    "COLOR_BLUE_HOVER",
    "COLOR_GREEN",
    "COLOR_GREEN_HOVER",
    "COLOR_GREY",
    "COLOR_GREY_HOVER",
    "COLOR_RED",
    "COLOR_RED_ACTIVE",
    "COLOR_RED_HOVER",
    "STATUS_GRAY",
    "STATUS_GREEN",
    "STATUS_ORANGE",
    "pick_emoji_font_family",
    "ui_font",
]

logger = logging.getLogger(__name__)

COLOR_RED = "#D32F2F"
COLOR_RED_HOVER = "#B71C1C"
COLOR_RED_ACTIVE = "#8E0000"
COLOR_GREY = "#3A3A3A"
COLOR_GREY_HOVER = "#4A4A4A"
COLOR_BLUE = "#1F6AA5"
COLOR_BLUE_HOVER = "#144870"
COLOR_ACCENT = "#3B8ED0"
COLOR_GREEN = "#2E7D32"
COLOR_GREEN_HOVER = "#1B5E20"

#: Status-bar colours by meaning.
STATUS_GRAY = "gray"
STATUS_GREEN = "green"
STATUS_ORANGE = "orange"


def pick_emoji_font_family() -> str | None:
    """Return a font family that renders colour emoji, if one is installed.

    Tk otherwise falls back to a monochrome glyph and 🟢 / 🔵 become identical
    grey circles. Requires a live Tk root because ``tkfont.families()`` does.
    """
    candidates = ("Segoe UI Emoji", "Noto Color Emoji", "Apple Color Emoji")
    try:
        import tkinter.font as tkfont

        available = set(tkfont.families())
    except Exception:
        return None
    for family in candidates:
        if family in available:
            return family
    return None


def ui_font(family: str | None, size: int = 13, bold: bool = False) -> ctk.CTkFont:
    """Build a UI font, optionally with the colour-emoji family."""
    weight = "bold" if bold else "normal"
    if family:
        return ctk.CTkFont(family=family, size=size, weight=weight)
    return ctk.CTkFont(size=size, weight=weight)
