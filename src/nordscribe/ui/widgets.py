"""Reusable UI widgets.

The copy button was duplicated in the transcription and history tabs (clipboard
handling, 1.5-second "Copied!" feedback, timer cancellation). It lives here
once so both tabs behave identically.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable

import customtkinter as ctk

from nordscribe.ui.theme import (
    COLOR_GREEN,
    COLOR_GREEN_HOVER,
    COLOR_GREY,
    COLOR_GREY_HOVER,
)

__all__ = ["COPIED_LABEL", "COPY_LABEL", "CopyButton"]

logger = logging.getLogger(__name__)

COPY_LABEL = "Копировать 📋"
COPIED_LABEL = "Скопировано! ✅"

_FEEDBACK_MS = 1500


class CopyButton(ctk.CTkButton):
    """A button that copies a value and shows short visual feedback."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        *,
        get_text: Callable[[], str],
        on_empty: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        width: int = 150,
        height: int = 45,
        font: ctk.CTkFont | None = None,
    ) -> None:
        super().__init__(
            master,
            text=COPY_LABEL,
            command=self._copy,
            width=width,
            height=height,
            font=font,
            fg_color=COLOR_GREY,
            hover_color=COLOR_GREY_HOVER,
        )
        self._get_text = get_text
        self._on_empty = on_empty
        self._on_error = on_error
        self._after_id: str | None = None

    def _copy(self) -> None:
        text = self._get_text().strip()
        if not text:
            if self._on_empty is not None:
                self._on_empty("Нечего копировать — поле пустое")
            return

        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
        except tk.TclError as exc:
            logger.warning("clipboard is unavailable: %s", exc)
            if self._on_error is not None:
                self._on_error("Буфер обмена недоступен")
            return

        self._show_copied()

    def _show_copied(self) -> None:
        if self._after_id is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._after_id)

        self.configure(
            text=COPIED_LABEL,
            fg_color=COLOR_GREEN,
            hover_color=COLOR_GREEN_HOVER,
        )
        self._after_id = self.after(_FEEDBACK_MS, self._restore)

    def _restore(self) -> None:
        self._after_id = None
        try:
            if self.winfo_exists():
                self.configure(
                    text=COPY_LABEL,
                    fg_color=COLOR_GREY,
                    hover_color=COLOR_GREY_HOVER,
                )
        except tk.TclError:
            pass  # the card was destroyed in the meantime
