"""
Палитра и шрифтовые утилиты интерфейса.

Приложение всегда работает в тёмной теме, поэтому цвета заданы явно
и собраны в одном месте: :mod:`gui_components` и виджеты вкладок берут
их отсюда, а не дублируют константы.
"""

from __future__ import annotations

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
    "ui_font",
]

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

#: Цвета строки состояния по смыслу сообщения.
STATUS_GRAY = "gray"
STATUS_GREEN = "green"
STATUS_ORANGE = "orange"


def ui_font(family: str | None, size: int = 13, bold: bool = False) -> ctk.CTkFont:
    """
    Шрифт интерфейса с поддержкой цветных эмодзи.

    ``family`` — результат :func:`gui_components.pick_emoji_font_family`:
    если в системе есть шрифт с цветными эмодзи, он подставляется — иначе
    значки вроде 📋 рисуются пустым квадратом.
    """
    weight = "bold" if bold else "normal"
    if family:
        return ctk.CTkFont(family=family, size=size, weight=weight)
    return ctk.CTkFont(size=size, weight=weight)
