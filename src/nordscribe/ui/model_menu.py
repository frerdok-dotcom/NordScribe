"""Colouring individual items of the model dropdown.

CustomTkinter's ``CTkOptionMenu`` hides a ``tkinter.Menu`` behind a private
``_dropdown_menu`` attribute. The menu is rebuilt on every ``configure(values=)``
call, so colours have to be applied after each refresh. If a future
CustomTkinter renames the attribute, a warning is logged once instead of
silently losing the status colours.
"""

from __future__ import annotations

import logging
import tkinter as tk
from collections.abc import Iterable, Sequence
from typing import Any

from nordscribe.core.models import MENU_COLOR_DOWNLOADED, MENU_COLOR_MISSING

__all__ = ["apply_menu_item_colors", "find_dropdown_menu"]

logger = logging.getLogger(__name__)

_dropdown_warned = False


def find_dropdown_menu(option_menu: Any) -> tk.Menu | None:
    """Return the internal ``tk.Menu`` of a ``CTkOptionMenu``."""
    global _dropdown_warned

    menu = getattr(option_menu, "_dropdown_menu", None)
    if menu is None and not _dropdown_warned:
        logger.warning(
            "CTkOptionMenu has no _dropdown_menu attribute — menu colours are "
            "disabled (did CustomTkinter change?)"
        )
        _dropdown_warned = True
    return menu


def apply_menu_item_colors(
    option_menu: Any,
    models: Sequence[str],
    downloaded: Iterable[str],
) -> None:
    """Colour menu items by model availability.

    Green means the weights are cached, blue means they must be downloaded.
    The text colour duplicates the emoji marker because colour emoji are not
    available in every Tk build.
    """
    menu = find_dropdown_menu(option_menu)
    if menu is None:
        return
    available = set(downloaded)
    for index, model in enumerate(models):
        color = MENU_COLOR_DOWNLOADED if model in available else MENU_COLOR_MISSING
        try:
            menu.entryconfigure(index, foreground=color)
        except tk.TclError:
            break  # the menu has fewer items than the label list
