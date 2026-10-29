"""Thread-safe bridge from worker threads to Tk's main loop.

Tk widgets may only be touched from the thread that created them. Worker
threads call :meth:`UiDispatcher.post`; the dispatcher drains a queue from a
periodic ``after`` callback on the UI thread.
"""

from __future__ import annotations

import logging
import queue
import tkinter as tk
from collections.abc import Callable
from contextlib import suppress

__all__ = ["UiDispatcher"]

logger = logging.getLogger(__name__)

Callback = Callable[..., None]


class UiDispatcher:
    """A small queue drained on the Tk main loop."""

    def __init__(self, widget: tk.Misc, interval_ms: int = 33) -> None:
        self._widget = widget
        self._interval_ms = interval_ms
        self._queue: queue.SimpleQueue[tuple[Callback, tuple[object, ...]]] = queue.SimpleQueue()
        self._after_id: str | None = None
        self._running = False

    def start(self) -> None:
        """Start draining the queue."""
        if self._running:
            return
        self._running = True
        self._schedule()

    def stop(self) -> None:
        """Stop draining; queued callbacks are discarded."""
        self._running = False
        after_id, self._after_id = self._after_id, None
        if after_id is not None:
            with suppress(tk.TclError):
                self._widget.after_cancel(after_id)

    def post(self, callback: Callback, *args: object) -> None:
        """Schedule ``callback(*args)`` on the UI thread.

        Safe to call from any thread. Calls after :meth:`stop` are dropped.
        """
        if not self._running:
            logger.debug("UI dispatcher is stopped; dropping %r", callback)
            return
        self._queue.put((callback, args))

    def _schedule(self) -> None:
        if not self._running or self._after_id is not None:
            return
        with suppress(tk.TclError):
            self._after_id = self._widget.after(self._interval_ms, self._drain)

    def _drain(self) -> None:
        self._after_id = None
        if not self._running:
            return
        while True:
            try:
                callback, args = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args)
            except Exception:
                logger.exception("unhandled exception in a UI callback")
        self._schedule()
