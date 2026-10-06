"""
Вкладка «Транскрибация»: поле вывода, кнопки и индикатор прогресса.

Модуль отвечает только за внешний вид: виджеты создаются внутри
переданного контейнера, а все обработчики передаются параметрами
``on_load_audio`` и ``on_toggle_recording``. Никакой бизнес-логики
(файловые диалоги, потоки, история) здесь нет — она остаётся
в :mod:`main`.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable

import customtkinter as ctk

from theme import (
    COLOR_GREEN,
    COLOR_GREEN_HOVER,
    COLOR_GREY,
    COLOR_GREY_HOVER,
    COLOR_RED,
    COLOR_RED_ACTIVE,
    COLOR_RED_HOVER,
    STATUS_GRAY,
    ui_font,
)

logger = logging.getLogger(__name__)

__all__ = ["TranscriptionTab"]

#: Текст строки состояния, когда задача ещё не начата.
IDLE_STATUS = "Готов к работе"

#: Надписи кнопки копирования: обычная / подтверждение.
COPY_LABEL = "Копировать 📋"
COPIED_LABEL = "Скопировано! ✅"


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

        # Кнопка копирования результата: третьим элементом в ряду кнопок.
        self.frame_controls.grid_columnconfigure(2, weight=0)
        self._copy_timer: dict[str, str | None] = {"after_id": None}
        self.btn_copy = ctk.CTkButton(
            self.frame_controls,
            text=COPY_LABEL,
            command=self.copy_transcript,
            width=150,
            height=45,
            font=ui_font(self._emoji_family, size=14),
            fg_color=COLOR_GREY,
            hover_color=COLOR_GREY_HOVER,
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

    def copy_transcript(self) -> None:
        """
        Копирует весь текст из поля вывода в системный буфер обмена.

        Пустое поле не копируется — вместо этого показывается подсказка.
        При успехе кнопка на 1.5 секунды становится зелёной с надписью
        «Скопировано! ✅»; повторный клик перезапускает таймер,
        а не наслаивает второй.
        """
        text = self.txt_transcript.get("1.0", "end-1c").strip()
        if not text:
            self.set_status("Нечего копировать — поле пустое", color=STATUS_GRAY)
            return

        button = self.btn_copy
        try:
            button.clipboard_clear()
            button.clipboard_append(text)
            button.update_idletasks()
        except tk.TclError as exc:
            logger.warning("буфер обмена недоступен: %s", exc)
            self.set_status("Буфер обмена недоступен", color=COLOR_RED)
            return

        previous = self._copy_timer.get("after_id")
        if previous:
            with contextlib.suppress(tk.TclError):
                button.after_cancel(previous)

        button.configure(
            text=COPIED_LABEL,
            fg_color=COLOR_GREEN,
            hover_color=COLOR_GREEN_HOVER,
        )

        def restore() -> None:
            self._copy_timer["after_id"] = None
            try:
                if button.winfo_exists():
                    button.configure(
                        text=COPY_LABEL,
                        fg_color=COLOR_GREY,
                        hover_color=COLOR_GREY_HOVER,
                    )
            except tk.TclError:
                pass  # виджет успели уничтожить

        self._copy_timer["after_id"] = button.after(1500, restore)

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
