"""Transcription tab: output box, action buttons and progress indicator.

The widget owns presentation only. File dialogs, recording, transcription and
history live in :mod:`nordscribe.ui.main_window`; handlers are injected as
callables.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import customtkinter as ctk

from nordscribe.ui.theme import (
    COLOR_RED,
    COLOR_RED_ACTIVE,
    COLOR_RED_HOVER,
    STATUS_GRAY,
    ui_font,
)
from nordscribe.ui.widgets import CopyButton

__all__ = ["IDLE_STATUS", "TranscriptionTab"]

logger = logging.getLogger(__name__)

#: Status text before any task starts.
IDLE_STATUS = "Готов к работе"


class TranscriptionTab:
    """All widgets of the transcription tab, in one place."""

    RECORD_LABEL = "🎤 Записать с микрофона"
    STOP_LABEL = "⏹ Остановить запись"

    def __init__(
        self,
        parent: ctk.CTkTabview | ctk.CTkFrame,
        *,
        emoji_family: str | None,
        on_load_audio: Callable[[], None],
        on_toggle_recording: Callable[[], None],
    ) -> None:
        self.parent = parent
        self._emoji_family = emoji_family

        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)  # transcript box grows

        self.txt_transcript = ctk.CTkTextbox(
            parent,
            wrap="word",
            font=ctk.CTkFont(size=14),
        )
        self.txt_transcript.grid(row=0, column=0, sticky="nsew", pady=(4, 10))

        self.frame_controls = ctk.CTkFrame(parent, fg_color="transparent")
        self.frame_controls.grid(row=1, column=0, sticky="ew")
        self.frame_controls.grid_columnconfigure((0, 1), weight=1)
        self.frame_controls.grid_columnconfigure(2, weight=0)

        self.btn_load_audio = ctk.CTkButton(
            self.frame_controls,
            text="📁 Загрузить аудиофайл",
            command=on_load_audio,
            height=45,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.btn_load_audio.grid(row=0, column=0, padx=(0, 10), pady=(0, 10), sticky="ew")

        self.btn_record_mic = ctk.CTkButton(
            self.frame_controls,
            text=self.RECORD_LABEL,
            command=on_toggle_recording,
            fg_color=COLOR_RED,
            hover_color=COLOR_RED_HOVER,
            height=45,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.btn_record_mic.grid(row=0, column=1, padx=(10, 0), pady=(0, 10), sticky="ew")

        self.btn_copy = CopyButton(
            self.frame_controls,
            get_text=lambda: self.txt_transcript.get("1.0", "end-1c"),
            on_empty=lambda message: self.set_status(message, STATUS_GRAY),
            on_error=lambda message: self.set_status(message, COLOR_RED),
            width=150,
            height=45,
            font=ui_font(self._emoji_family, size=14),
        )
        self.btn_copy.grid(row=0, column=2, padx=(10, 0), pady=(0, 10), sticky="ew")

        self.progress_bar = ctk.CTkProgressBar(self.frame_controls, height=10)
        self.progress_bar.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(5, 10))
        self.progress_bar.set(0)

        self.lbl_status = ctk.CTkLabel(
            self.frame_controls,
            text=IDLE_STATUS,
            text_color=STATUS_GRAY,
            font=ctk.CTkFont(size=13),
        )
        self.lbl_status.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(0, 5))

    # -- presentation ---------------------------------------------------

    def set_status(self, message: str, color: str = STATUS_GRAY) -> None:
        """Show a message in the status label."""
        self.lbl_status.configure(text=message, text_color=color)

    def clear_transcript(self) -> None:
        """Clear the output box."""
        self.txt_transcript.delete("1.0", "end")

    def show_transcript(self, text: str) -> None:
        """Replace the output box with the transcription."""
        self.txt_transcript.insert("1.0", text)

    def set_recording(self, active: bool) -> None:
        """Switch the record button between "record" and "stop" modes."""
        if active:
            self.btn_record_mic.configure(
                text=self.STOP_LABEL,
                fg_color=COLOR_RED_HOVER,
                hover_color=COLOR_RED_ACTIVE,
            )
        else:
            self.btn_record_mic.configure(
                text=self.RECORD_LABEL,
                fg_color=COLOR_RED,
                hover_color=COLOR_RED_HOVER,
            )

    def start_progress_animation(self) -> None:
        """Show an indeterminate bar: Whisper does not report progress."""
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start()

    def stop_progress_animation(self, value: float | None = None) -> None:
        """Return the bar to determinate mode and optionally set a value."""
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        if value is not None:
            self.progress_bar.set(value)
