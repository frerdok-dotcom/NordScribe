"""History tab: cards of past transcriptions and a clear button.

The widget displays ready-made :class:`~nordscribe.infra.history.HistoryEntry`
objects; it does not touch SQLite. Mouse-wheel behaviour lives in
:mod:`nordscribe.ui.mousewheel`, clipboard feedback in
:mod:`nordscribe.ui.widgets`.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable, Sequence

import customtkinter as ctk

from nordscribe.infra.history import HistoryEntry, format_local_time
from nordscribe.ui.mousewheel import ScrollController
from nordscribe.ui.theme import (
    COLOR_GREY,
    COLOR_GREY_HOVER,
    COLOR_RED,
    COLOR_RED_HOVER,
    STATUS_GRAY,
    ui_font,
)
from nordscribe.ui.widgets import CopyButton

__all__ = ["HistoryTab"]

logger = logging.getLogger(__name__)

#: How long the clear confirmation stays armed.
CLEAR_CONFIRM_MS = 5000

#: Normal and armed labels of the clear button.
CLEAR_LABEL = "Очистить историю 🗑"
CLEAR_ARMED_LABEL = "Точно очистить?"


class HistoryTab:
    """Scrollable list of transcription cards."""

    def __init__(
        self,
        parent: ctk.CTkTabview | ctk.CTkFrame,
        *,
        emoji_family: str | None,
        on_cleared: Callable[[], None] | None = None,
    ) -> None:
        self._emoji_family = emoji_family
        self._on_cleared = on_cleared

        self._first: ctk.CTkFrame | None = None
        self._placeholder: ctk.CTkFrame | None = None
        self._clear_armed = False
        self._clear_timer: str | None = None

        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        header.grid_columnconfigure(0, weight=1)

        self.btn_clear = ctk.CTkButton(
            header,
            text=CLEAR_LABEL,
            width=190,
            height=28,
            font=ui_font(emoji_family, size=12),
            fg_color=COLOR_GREY,
            hover_color=COLOR_GREY_HOVER,
            command=self.on_clear_click,
        )
        self.btn_clear.grid(row=0, column=1, sticky="e")

        self.scroll_frame = ctk.CTkScrollableFrame(parent)
        self.scroll_frame.grid(row=1, column=0, sticky="nsew")
        self._scroll = ScrollController(self.scroll_frame)
        self._scroll.attach()

    # -- list contents --------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        """Disable the clear button when the database is unavailable."""
        self.btn_clear.configure(state="normal" if enabled else "disabled")

    def load(self, entries: Sequence[HistoryEntry]) -> None:
        """Replace the list with ``entries``; show a placeholder when empty."""
        self.clear_cards()
        for entry in entries:
            card = self.create_card(entry)
            card.pack(fill="x", padx=8, pady=(6, 0))
            if self._first is None:
                self._first = card
        if not entries:
            self.show_placeholder("История пуста")
        self._scroll.rebind()

    def prepend(self, entry: HistoryEntry) -> None:
        """Add a card at the top without restarting the app."""
        self.cancel_clear()
        self.hide_placeholder()
        card = self.create_card(entry)
        first = self._first
        if first is not None and first.winfo_exists():
            card.pack(fill="x", padx=8, pady=(6, 0), before=first)
        else:
            card.pack(fill="x", padx=8, pady=(6, 0))
        self._first = card
        self._scroll.rebind()

    def clear_cards(self) -> None:
        """Remove every card and the placeholder."""
        for widget in self.scroll_frame.pack_slaves():
            with contextlib.suppress(tk.TclError):
                widget.destroy()
        self._first = None
        self._placeholder = None
        self._scroll.rebind()

    def show_placeholder(self, text: str) -> None:
        """Show a grey placeholder while the list is empty."""
        self.hide_placeholder()
        placeholder = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        placeholder.pack(fill="x", padx=8, pady=20)
        ctk.CTkLabel(
            placeholder,
            text=text,
            text_color=STATUS_GRAY,
            font=ctk.CTkFont(size=13),
        ).pack()
        self._placeholder = placeholder
        if self._first is None:
            self._first = placeholder

    def hide_placeholder(self) -> None:
        """Remove the placeholder when the first real card appears."""
        placeholder = self._placeholder
        if placeholder is None:
            return
        if self._first is placeholder:
            self._first = None
        self._placeholder = None
        if placeholder is not None:
            with contextlib.suppress(tk.TclError):
                placeholder.destroy()

    def disable_mousewheel(self) -> None:
        """Remove wheel bindings before the window closes."""
        self._scroll.detach()

    # -- one card -------------------------------------------------------

    def create_card(self, entry: HistoryEntry) -> ctk.CTkFrame:
        """Build a card for one history entry."""
        card = ctk.CTkFrame(self.scroll_frame, corner_radius=6)
        card.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text=format_local_time(entry.created_at),
            font=ctk.CTkFont(size=11),
            text_color=STATUS_GRAY,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        copy_button = CopyButton(
            header,
            get_text=lambda: entry.text,
            width=150,
            height=26,
            font=ui_font(self._emoji_family, size=12),
        )
        copy_button.grid(row=0, column=1, sticky="e")

        text_box = ctk.CTkTextbox(card, height=74, wrap="word", font=ctk.CTkFont(size=13))
        # Insert before disabling: a disabled textbox ignores inserts. The
        # field stays read-only but text can still be selected and copied.
        text_box.insert("1.0", entry.text)
        text_box.configure(state="disabled")
        text_box.grid(row=1, column=0, sticky="ew", padx=10, pady=(6, 10))
        return card

    # -- clear with confirmation ---------------------------------------

    def on_clear_click(self) -> None:
        """First click arms the confirmation; the second one clears."""
        if not self._clear_armed:
            self.arm_clear()
            return
        self.cancel_clear()
        if self._on_cleared is not None:
            self._on_cleared()

    def arm_clear(self) -> None:
        """Arm the confirmation for ``CLEAR_CONFIRM_MS``."""
        self.cancel_clear()
        self._clear_armed = True
        self.btn_clear.configure(
            text=CLEAR_ARMED_LABEL,
            fg_color=COLOR_RED,
            hover_color=COLOR_RED_HOVER,
        )
        self._clear_timer = self.btn_clear.after(CLEAR_CONFIRM_MS, self.cancel_clear)

    def cancel_clear(self) -> None:
        """Disarm the confirmation and restore the button."""
        if self._clear_timer is not None:
            with contextlib.suppress(tk.TclError):
                self.btn_clear.after_cancel(self._clear_timer)
            self._clear_timer = None
        if not self._clear_armed:
            return
        self._clear_armed = False
        with contextlib.suppress(tk.TclError):  # the window may be closing
            if self.btn_clear.winfo_exists():
                self.btn_clear.configure(
                    text=CLEAR_LABEL,
                    fg_color=COLOR_GREY,
                    hover_color=COLOR_GREY_HOVER,
                )
