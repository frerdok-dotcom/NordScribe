"""Predictable mouse-wheel scrolling for ``CTkScrollableFrame``.

CustomTkinter's default wheel handling binds ``bind_all`` and scrolls by
``yscrollincrement`` units, which behaves differently on Windows and X11 and
also steals the wheel from other widgets. This controller owns a single global
handler, decides by widget ancestry whether the event belongs to its list, and
scrolls a fixed fraction of the viewport.

The controller is deliberately self-contained: the history tab only calls
``attach()``, ``rebind()`` and ``detach()``. The pure helpers
(:func:`wheel_direction`, :func:`is_descendant`) are tested without a display
where possible.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import customtkinter as ctk

__all__ = [
    "WHEEL_SEQUENCES",
    "ScrollController",
    "is_descendant",
    "wheel_direction",
]

logger = logging.getLogger(__name__)

#: Wheel event sequences on Windows/macOS and Linux/X11.
WHEEL_SEQUENCES = ("<MouseWheel>", "<Button-4>", "<Button-5>")


@dataclass(slots=True)
class _WheelState:
    """Accumulated wheel movement for one Tk tick."""

    pending: int = 0
    flush_id: str | None = None


#: Scroll step as a fraction of the visible list height.
WHEEL_SCROLL_FRACTION = 0.15

#: Minimum and maximum step in pixels.
WHEEL_SCROLL_MIN_PX = 48
WHEEL_SCROLL_MAX_PX = 120

#: How many accumulated wheel events are executed per Tk tick. One step
#: prevents a burst of events from jumping across the whole list.
WHEEL_MAX_STEPS_PER_FLUSH = 1

#: Delay before the shared flush (milliseconds). Events from one Tk tick are
#: collapsed into a single step.
WHEEL_FLUSH_MS = 0


def wheel_direction(event: object) -> int:
    """Return -1 for up, +1 for down and 0 when the event is not a wheel.

    Windows/macOS send ``delta``; Linux/X11 sends virtual buttons 4 and 5.
    The magnitude is intentionally ignored: one wheel movement is one step.
    """
    delta = getattr(event, "delta", 0)
    if delta:
        return -1 if delta > 0 else 1
    button = getattr(event, "num", 0)
    if button == 4:
        return -1
    if button == 5:
        return 1
    return 0


def destroyed(widget: tk.Misc) -> bool:
    """Whether ``widget`` no longer exists and must not be touched."""
    try:
        return not widget.winfo_exists()
    except tk.TclError:
        return True


def is_descendant(widget: tk.Misc | None, container: tk.Misc) -> bool:
    """Whether ``widget`` is ``container`` or one of its descendants.

    Widget-tree ancestry is more accurate than coordinates: the clear button
    overlaps the list area, so "the event came from the list" must be decided
    by the tree, not by the pointer position.
    """
    current = widget
    while current is not None:
        if current is container:
            return True
        if destroyed(current):
            return False
        try:
            current = current.master
        except tk.TclError:
            return False
    return False


class ScrollController:
    """Attach custom wheel behaviour to one scrollable frame."""

    def __init__(self, scroll_frame: ctk.CTkScrollableFrame) -> None:
        self._frame = scroll_frame
        self._bindings: list[tuple[tk.Misc, str]] = []
        self._all_sequences: tuple[str, ...] = ()
        self._rebind: Callable[[], None] = lambda: None
        self._canvas: tk.Canvas | None = None
        self._handler: Callable[[tk.Event[Any]], str | None] | None = None

    # -- public API -----------------------------------------------------

    def attach(self) -> None:
        """Install the global wheel handler and bind the list widgets."""
        canvas = self._find_scroll_canvas()
        if canvas is None:
            logger.warning("history canvas not found — using default wheel scrolling")
            return

        self._canvas = canvas
        toplevel = canvas.winfo_toplevel()

        # The list itself and the scrollbar (which lives outside the frame but
        # belongs to the same list) are the regions that scroll it.
        wheel_regions: list[tk.Misc] = [self._frame]

        def in_wheel_regions(widget: tk.Misc | None) -> bool:
            return any(is_descendant(widget, region) for region in wheel_regions)

        # Remove CustomTkinter's global handler: otherwise any wheel movement
        # anywhere in the window scrolls this list.
        for sequence in WHEEL_SEQUENCES:
            with contextlib.suppress(tk.TclError):
                toplevel.unbind_all(sequence)

        state = _WheelState()

        def scroll_by(direction: int) -> None:
            with contextlib.suppress(tk.TclError):
                bbox = canvas.bbox("all")
                if not bbox or bbox[3] <= 0:
                    return
                height_px = float(bbox[3])
                top, bottom = canvas.yview()
                view = bottom - top
                if view >= 1.0:
                    return
                viewport_px = float(canvas.winfo_height())
                step_px = min(
                    max(viewport_px * WHEEL_SCROLL_FRACTION, WHEEL_SCROLL_MIN_PX),
                    WHEEL_SCROLL_MAX_PX,
                )
                new_top = min(
                    max(top + direction * step_px / height_px, 0.0),
                    max(1.0 - view, 0.0),
                )
                canvas.yview_moveto(new_top)

        def flush() -> None:
            state.flush_id = None
            pending = state.pending
            state.pending = 0
            if not pending or destroyed(canvas):
                return
            direction = 1 if pending > 0 else -1
            for _ in range(min(abs(pending), WHEEL_MAX_STEPS_PER_FLUSH)):
                scroll_by(direction)

        def schedule_flush() -> None:
            if state.flush_id is None:
                state.flush_id = canvas.after(WHEEL_FLUSH_MS, flush)

        def on_wheel(event: tk.Event[Any]) -> str | None:
            if not in_wheel_regions(getattr(event, "widget", None)):
                return None
            direction = wheel_direction(event)
            if not direction:
                return "break"
            state.pending += direction
            schedule_flush()
            return "break"

        self._handler = on_wheel
        for sequence in WHEEL_SEQUENCES:
            self._bindings.append((canvas, canvas.bind(sequence, on_wheel, add="")))

        self._rebind = lambda: self._rebind_tree(canvas, on_wheel)
        self._rebind()

        scrollbar = getattr(self._frame, "_scrollbar", None)
        scrollbar_canvas = getattr(scrollbar, "_canvas", None)
        if isinstance(scrollbar_canvas, tk.Canvas):
            wheel_regions.append(scrollbar_canvas)
            for sequence in WHEEL_SEQUENCES:
                bound = scrollbar_canvas.bind(sequence, on_wheel, add="")
                self._bindings.append((scrollbar_canvas, bound))

        self._configure_scroll_increment()
        canvas.bind("<Configure>", self._on_canvas_configure, add="+")

        # The handler is global but silent for widgets outside the list.
        self._all_sequences = WHEEL_SEQUENCES
        for sequence in WHEEL_SEQUENCES:
            toplevel.bind_all(sequence, on_wheel, add="")

    def rebind(self) -> None:
        """Re-attach to text widgets after cards are created or destroyed."""
        self._rebind()

    def detach(self) -> None:
        """Remove all bindings; safe to call more than once."""
        for widget, _funcid in self._bindings:
            if destroyed(widget):
                continue
            with contextlib.suppress(tk.TclError):
                for sequence in WHEEL_SEQUENCES:
                    widget.unbind(sequence)
        self._bindings = []
        self._rebind = lambda: None
        self._handler = None

        if self._all_sequences:
            canvas = self._canvas
            if canvas is not None and not destroyed(canvas):
                with contextlib.suppress(tk.TclError):
                    toplevel = canvas.winfo_toplevel()
                    for sequence in self._all_sequences:
                        toplevel.unbind_all(sequence)
            self._all_sequences = ()
        self._canvas = None

    # -- internals ------------------------------------------------------

    def _on_canvas_configure(self, _event: tk.Event[Any]) -> None:
        self._configure_scroll_increment()

    def _configure_scroll_increment(self) -> None:
        """Give the scrollbar a visible step (Windows defaults to 1 px)."""
        canvas = self._find_scroll_canvas()
        if canvas is None or destroyed(canvas):
            return
        with contextlib.suppress(tk.TclError):
            viewport_px = max(canvas.winfo_height(), WHEEL_SCROLL_MIN_PX)
            step_px = int(min(max(viewport_px * 0.05, 8), 40))
            if int(canvas.cget("yscrollincrement") or 0) != step_px:
                canvas.configure(yscrollincrement=step_px)

    def _find_canvas(self, widget: tk.Misc) -> tk.Canvas | None:
        if widget.winfo_class() == "Canvas":
            return widget  # type: ignore[return-value]
        for child in widget.winfo_children():
            found = self._find_canvas(child)
            if found is not None:
                return found
        return None

    def _find_scroll_canvas(self) -> tk.Canvas | None:
        """Locate CustomTkinter's internal canvas across versions."""
        for attr in ("_parent_canvas", "_canvas"):
            canvas = getattr(self._frame, attr, None)
            if isinstance(canvas, tk.Canvas):
                return canvas
        return self._find_canvas(self._frame)

    def _rebind_tree(
        self,
        canvas: tk.Canvas,
        handler: Callable[[tk.Event[Any]], str | None],
    ) -> None:
        """Bind wheel events on card text boxes; drop dead bindings first."""
        canvas_path = str(canvas)
        alive: list[tuple[tk.Misc, str]] = []
        for widget, funcid in self._bindings:
            if destroyed(widget):
                continue
            if widget is not canvas and str(widget).startswith(canvas_path):
                with contextlib.suppress(tk.TclError):
                    for sequence in WHEEL_SEQUENCES:
                        widget.unbind(sequence)
                continue
            alive.append((widget, funcid))
        self._bindings = alive

        for child in canvas.winfo_children():
            self._rebind_widget_tree(child, handler)

    def _rebind_widget_tree(
        self,
        widget: tk.Misc,
        handler: Callable[[tk.Event[Any]], str | None],
    ) -> None:
        if widget.winfo_class() in ("CTkTextbox", "Text"):
            for sequence in WHEEL_SEQUENCES:
                self._bindings.append((widget, widget.bind(sequence, handler, add="")))
        for child in widget.winfo_children():
            self._rebind_widget_tree(child, handler)
