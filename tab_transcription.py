"""
Вкладка «Транскрибация»: поле вывода, кнопки и индикатор прогресса.

Модуль отвечает только за внешний вид: виджеты создаются внутри
переданного контейнера, а все обработчики передаются параметрами
``on_load_audio`` и ``on_toggle_recording``. Никакой бизнес-логики
(файловые диалоги, потоки, история) здесь нет — она остаётся
в :mod:`main`.
"""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

from theme import (
    COLOR_RED,
    COLOR_RED_ACTIVE,
    COLOR_RED_HOVER,
    STATUS_GRAY,
)

__all__ = ["TranscriptionTab"]

#: Текст строки состояния, когда задача ещё не начата.
IDLE_STATUS = "Готов к работе"


class TranscriptionTab:
    """Виджеты вкладки транскрибации, собранные в одном месте."""

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
        parent.grid_rowconfigure(1, weight=1)  # растягивается поле вывода

        self.lbl_transcript = ctk.CTkLabel(
            parent,
            text="🎙️ Транскрибация (Whisper)",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        self.lbl_transcript.grid(row=0, column=0, sticky="nw", pady=(4, 10))

        self.txt_transcript = ctk.CTkTextbox(parent, wrap="word", font=ctk.CTkFont(size=14))
        self.txt_transcript.grid(row=1, column=0, sticky="nsew", pady=(0, 10))

        self.frame_controls = ctk.CTkFrame(parent, fg_color="transparent")
        self.frame_controls.grid(row=2, column=0, sticky="ew")
        self.frame_controls.grid_columnconfigure((0, 1), weight=1)

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

        self.progress_bar = ctk.CTkProgressBar(self.frame_controls, height=10)
        self.progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 10))
        self.progress_bar.set(0)

        self.lbl_status = ctk.CTkLabel(
            self.frame_controls,
            text=IDLE_STATUS,
            text_color=STATUS_GRAY,
            font=ctk.CTkFont(size=13),
        )
        self.lbl_status.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 5))

    # надписи кнопки записи: обычная / идущая запись
    RECORD_LABEL = "🎤 Записать с микрофона"
    STOP_LABEL = "⏹ Остановить запись"

    def set_status(self, message: str, color: str = STATUS_GRAY) -> None:
        """Показывает сообщение в строке состояния."""
        self.lbl_status.configure(text=message, text_color=color)

    def clear_transcript(self) -> None:
        """Очищает поле вывода."""
        self.txt_transcript.delete("1.0", "end")

    def show_transcript(self, text: str) -> None:
        """Вставляет результат распознавания в поле вывода."""
        self.txt_transcript.insert("1.0", text)

    def set_recording(self, active: bool) -> None:
        """Переключает кнопку записи между режимами «записать»/«остановить»."""
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
        """
        Включает бегущую полосу.

        Whisper не сообщает прогресс распознавания, поэтому честнее показать
        «идёт работа», чем неподвижную полосу на нуле.
        """
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start()

    def stop_progress_animation(self, value: float | None = None) -> None:
        """Возвращает полосу в обычный режим и при необходимости задаёт значение."""
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        if value is not None:
            self.progress_bar.set(value)

    def set_progress(self, value: float) -> None:
        """Устанавливает детерминированное значение прогресса."""
        self.progress_bar.set(value)
