"""Model manager window: download and delete Whisper weights.

The window keeps no model state of its own. :class:`~nordscribe.services.model_service.ModelService`
owns the cache and the downloader; the window only renders rows and forwards
user actions. Worker callbacks are posted through the main window's
:class:`~nordscribe.ui.dispatcher.UiDispatcher`.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import customtkinter as ctk

from nordscribe.core.errors import DownloadCancelled
from nordscribe.core.formatting import format_bytes
from nordscribe.core.models import ModelSpec
from nordscribe.services.model_service import ModelService
from nordscribe.ui.icon import apply_window_icon
from nordscribe.ui.theme import (
    COLOR_ACCENT,
    COLOR_BLUE,
    COLOR_BLUE_HOVER,
    COLOR_GREY,
    COLOR_GREY_HOVER,
    COLOR_RED,
    COLOR_RED_HOVER,
    STATUS_GRAY,
    STATUS_GREEN,
    STATUS_ORANGE,
)

__all__ = ["ModelManagerWindow"]

logger = logging.getLogger(__name__)

_DELETE_CONFIRM_MS = 5000


@dataclass(slots=True)
class _ModelRow:
    """Widgets of one model row."""

    size: ctk.CTkLabel
    status: ctk.CTkLabel
    button: ctk.CTkButton


class ModelManagerWindow(ctk.CTkToplevel):
    """Download and delete model files."""

    def __init__(
        self,
        master: Any,
        *,
        service: ModelService,
        post: Callable[[Callable[[], None]], None],
        on_models_changed: Callable[[], None],
        on_busy_changed: Callable[[bool], None],
        on_closed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._service = service
        self._post = post
        self._on_models_changed = on_models_changed
        self._on_busy_changed = on_busy_changed
        self._on_closed = on_closed

        apply_window_icon(self)
        self._alive = True
        self._downloaded: set[str] = set(service.downloaded())
        self._downloading: str | None = None
        self._cancel_requested = False
        self._pending_delete: str | None = None
        self._pending_delete_timer: str | None = None
        self._rows: dict[str, _ModelRow] = {}

        self.title("Диспетчер моделей Whisper")
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="Модели Whisper",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(14, 2))

        ctk.CTkLabel(
            self,
            text=f"Файлы сохраняются в {service.cache_dir}",
            font=ctk.CTkFont(size=11),
            text_color=STATUS_GRAY,
        ).grid(row=1, column=0, sticky="w", padx=15, pady=(0, 10))

        self.list_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.list_frame.grid(row=2, column=0, sticky="ew", padx=15)
        self.list_frame.grid_columnconfigure(0, weight=1)

        for index, spec in enumerate(service.specs):
            self._build_row(index, spec)

        self.progress_bar = ctk.CTkProgressBar(self, height=12)
        self.progress_bar.grid(row=3, column=0, sticky="ew", padx=15, pady=(14, 4))
        self.progress_bar.set(0)

        self.lbl_progress = ctk.CTkLabel(
            self,
            text="Выберите модель для загрузки",
            font=ctk.CTkFont(size=12),
            text_color=STATUS_GRAY,
            anchor="w",
            justify="left",
            wraplength=550,
        )
        self.lbl_progress.grid(row=4, column=0, sticky="ew", padx=15, pady=(0, 10))

        ctk.CTkButton(
            self,
            text="Закрыть",
            width=120,
            height=32,
            fg_color=COLOR_GREY,
            hover_color=COLOR_GREY_HOVER,
            command=self._on_close,
        ).grid(row=5, column=0, sticky="e", padx=15, pady=(0, 14))

        self._refresh_all()

        # Place the window over the parent after Tk has calculated sizes.
        self.update_idletasks()
        self._center_over_parent()
        self.after(10, self._bring_to_front)

    # -- construction ---------------------------------------------------

    def _build_row(self, index: int, spec: ModelSpec) -> None:
        row = ctk.CTkFrame(self.list_frame, corner_radius=6)
        row.grid(row=index, column=0, sticky="ew", pady=3)
        row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            row,
            text=spec.name,
            width=70,
            anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=(12, 6), pady=8)

        size_label = ctk.CTkLabel(
            row,
            text="",
            width=90,
            anchor="w",
            text_color=STATUS_GRAY,
            font=ctk.CTkFont(size=12),
        )
        size_label.grid(row=0, column=1, sticky="w", padx=(0, 10))

        status = ctk.CTkLabel(
            row,
            text="",
            width=130,
            anchor="e",
            font=ctk.CTkFont(size=12),
        )
        status.grid(row=0, column=2, sticky="e", padx=(0, 10))

        button = ctk.CTkButton(
            row,
            text="Скачать",
            width=140,
            height=30,
            command=lambda name=spec.name: self._on_row_action(name),
        )
        button.grid(row=0, column=3, sticky="e", padx=(0, 12), pady=6)

        self._rows[spec.name] = _ModelRow(size=size_label, status=status, button=button)

    def _refresh_all(self) -> None:
        """Redraw every row from the current cache state."""
        self._downloaded = self._service.downloaded()
        for spec in self._service.specs:
            row = self._rows[spec.name]
            downloaded = spec.name in self._downloaded
            size = self._service.size_of(spec.name)
            row.size.configure(text=format_bytes(size) if size else spec.size_label)

            if self._downloading == spec.name:
                row.status.configure(text="Загрузка...", text_color=COLOR_ACCENT)
                row.button.configure(
                    text="Скачивание...",
                    state="disabled",
                    fg_color=COLOR_GREY,
                    hover_color=COLOR_GREY,
                )
            elif downloaded:
                row.status.configure(text="Готова к работе", text_color=STATUS_GREEN)
                if self._pending_delete == spec.name:
                    row.button.configure(
                        text="Точно удалить?",
                        state="normal",
                        fg_color=COLOR_RED,
                        hover_color=COLOR_RED_HOVER,
                    )
                else:
                    row.button.configure(
                        text="Удалить",
                        state="normal",
                        fg_color=COLOR_GREY,
                        hover_color=COLOR_GREY_HOVER,
                    )
            else:
                row.status.configure(text="Не скачана", text_color=STATUS_GRAY)
                row.button.configure(
                    text="Скачать",
                    state="disabled" if self._downloading else "normal",
                    fg_color=COLOR_BLUE,
                    hover_color=COLOR_BLUE_HOVER,
                )

    # -- helpers --------------------------------------------------------

    def _center_over_parent(self) -> None:
        width, height = 600, 490
        self.geometry(f"{width}x{height}")
        parent = self.master
        try:
            x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - height) // 3
        except Exception:
            return
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _bring_to_front(self) -> None:
        if not self._alive:
            return
        self.lift()
        self.focus_force()

    def _set_message(self, text: str, color: str = STATUS_GRAY) -> None:
        self.lbl_progress.configure(text=text, text_color=color)

    # -- row actions ----------------------------------------------------

    def _on_row_action(self, name: str) -> None:
        if self._downloading is not None:
            return
        if name in self._downloaded:
            if self._pending_delete == name:
                self._cancel_pending_delete()
                self._delete_model(name)
            else:
                self._arm_pending_delete(name)
        else:
            self._start_download(name)

    def _arm_pending_delete(self, name: str) -> None:
        self._cancel_pending_delete()
        self._pending_delete = name
        self._refresh_all()
        self._set_message(
            f"Нажмите «Точно удалить?» ещё раз, чтобы удалить веса '{name}'",
            COLOR_RED,
        )
        self._pending_delete_timer = self.after(_DELETE_CONFIRM_MS, self._cancel_pending_delete)

    def _cancel_pending_delete(self) -> None:
        if self._pending_delete_timer is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._pending_delete_timer)
            self._pending_delete_timer = None
        if self._pending_delete is not None:
            self._pending_delete = None
            if self._alive:
                self._refresh_all()

    def _delete_model(self, name: str) -> None:
        try:
            result = self._service.delete(name)
        except OSError as exc:
            self._refresh_all()
            self._set_message(f"❌ Не удалось удалить '{name}': {exc}", COLOR_RED)
            return

        self._refresh_all()
        self.progress_bar.set(0)

        if not result.removed:
            self._set_message(f"Файлы модели '{name}' в кэше не найдены")
        elif result.still_available:
            self._set_message(
                f"Удалён {', '.join(result.removed_names)} "
                f"({format_bytes(result.freed_bytes)}), но модель '{name}' всё ещё "
                f"доступна — в кэше остались другие её веса.",
                STATUS_ORANGE,
            )
        else:
            self._set_message(
                f"✓ Модель '{name}' удалена ({', '.join(result.removed_names)}), "
                f"освобождено {format_bytes(result.freed_bytes)}",
                STATUS_GREEN,
            )
        self._on_models_changed()

    # -- download -------------------------------------------------------

    def _start_download(self, name: str) -> None:
        if self._downloading is not None:
            return
        self._cancel_pending_delete()

        if self._service.spec(name) is None:
            self._set_message(f"❌ Для модели '{name}' не найден адрес загрузки", COLOR_RED)
            return

        self._cancel_requested = False
        self._downloading = name
        self.progress_bar.set(0)
        self._set_message(f"Скачивание '{name}': подготовка...", COLOR_ACCENT)
        self._refresh_all()
        self._on_busy_changed(True)

        threading.Thread(
            target=self._download_worker,
            args=(name,),
            name=f"nordscribe-download-{name}",
            daemon=True,
        ).start()

    def _download_worker(self, name: str) -> None:
        """Network and disk work; widgets are updated through ``post``."""
        try:
            self._service.download(
                name,
                on_progress=lambda done, total: self._post(
                    lambda: self._show_progress(name, done, total)
                ),
                on_status=lambda text: self._post(lambda: self._set_message(text, COLOR_ACCENT)),
                is_cancelled=lambda: self._cancel_requested,
            )
        except DownloadCancelled:
            self._post(lambda: self._after_cancel(name))
        except Exception as exc:
            # ``exc`` is cleared when the except block exits; partial captures
            # the value before the callback runs on the UI thread.
            self._post(partial(self._after_error, name, exc))
        else:
            self._post(lambda: self._after_success(name))

    def _show_progress(self, name: str, done: int, total: int) -> None:
        if not self._alive or self._downloading != name:
            return
        if total > 0:
            fraction = min(done / total, 1.0)
            self.progress_bar.set(fraction)
            self._set_message(
                f"Скачивание '{name}': {fraction * 100:.0f}% "
                f"({format_bytes(done)} из {format_bytes(total)})",
                COLOR_ACCENT,
            )
        else:
            self.progress_bar.set(0)
            self._set_message(
                f"Скачивание '{name}': {format_bytes(done)}",
                COLOR_ACCENT,
            )

    def _after_success(self, name: str) -> None:
        # Clear the busy flag only on the UI thread. Clearing it in the worker
        # would let another download start before this update is processed.
        self._downloading = None
        if not self._alive:
            return
        self._refresh_all()
        self.progress_bar.set(1.0)
        self._set_message(
            f"✓ Модель '{name}' скачана и доступна для транскрибации",
            STATUS_GREEN,
        )
        self._on_busy_changed(False)
        self._on_models_changed()

    def _after_error(self, name: str, exc: BaseException) -> None:
        self._downloading = None
        if not self._alive:
            return

        spec = self._service.spec(name)
        resumable = (
            spec is not None and self._service.cache_dir.joinpath(f"{spec.filename}.part").exists()
        )
        self._refresh_all()
        self.progress_bar.set(0)
        tail = (
            "Повторите попытку — загрузка продолжится с места обрыва."
            if resumable
            else "Недокачанный файл удалён."
        )
        self._set_message(
            f"❌ Не удалось скачать '{name}': {self._service.describe_error(exc)}. {tail}",
            COLOR_RED,
        )
        self._on_busy_changed(False)

    def _after_cancel(self, name: str) -> None:
        self._downloading = None
        if not self._alive:
            return
        self._refresh_all()
        self.progress_bar.set(0)
        self._set_message(f"Загрузка '{name}' отменена, временный файл удалён")
        self._on_busy_changed(False)

    # -- closing --------------------------------------------------------

    def _on_close(self) -> None:
        if self._downloading is not None:
            self._cancel_requested = True
            self._set_message("Отмена загрузки...")
            self.after(100, self._wait_for_worker)
            return
        self._destroy()

    def _wait_for_worker(self) -> None:
        if not self._alive:
            return
        if self._downloading is not None:
            self.after(100, self._wait_for_worker)
            return
        self._destroy()

    def _destroy(self) -> None:
        self._alive = False
        self._cancel_pending_delete()
        self._on_busy_changed(False)
        if self._on_closed is not None:
            self._on_closed()
        self.destroy()
