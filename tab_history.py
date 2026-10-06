"""
Вкладка «История»: карточки прошлых транскрибаций и кнопка очистки.

Модуль отвечает за отображение: создание карточек, заглушку для пустого
списка, копирование текста в буфер обмена и подтверждение очистки
(«двойной клик» кнопкой). Хранилище (:mod:`history`) и база данных сюда
не передаются — виджет работает с готовыми объектами ``HistoryEntry``.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable, Sequence

import customtkinter as ctk

import history
from theme import (
    COLOR_GREEN,
    COLOR_GREEN_HOVER,
    COLOR_GREY,
    COLOR_GREY_HOVER,
    COLOR_RED,
    COLOR_RED_HOVER,
    STATUS_GRAY,
    ui_font,
)

__all__ = ["HistoryTab"]

logger = logging.getLogger(__name__)

#: Сколько ждать подтверждения очистки истории.
CLEAR_CONFIRM_MS = 5000

#: Надписи кнопки очистки в обычном режиме и во взведённом подтверждении.
CLEAR_LABEL = "Очистить историю 🗑"
CLEAR_ARMED_LABEL = "Точно очистить?"

#: Строк/единиц прокрутки на одно движение колеса мыши.
WHEEL_SCROLL_LINES = 3


def _wheel_direction(event: tk.Event) -> int:
    """Возвращает направление прокрутки: -1 — вверх, +1 — вниз.

    Учитывает различия платформ: Windows/macOS присылают ``delta``
    (кратный 120 или 1), Linux/X11 — виртуальные кнопки Button-4/5.
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


class HistoryTab:
    """Список карточек истории внутри прокручиваемой рамки."""

    def __init__(
        self,
        parent: ctk.CTkTabview | ctk.CTkFrame,
        *,
        emoji_family: str | None,
        on_cleared: Callable[[], int] | None = None,
    ) -> None:
        self._emoji_family = emoji_family
        self._on_cleared = on_cleared

        self._first: ctk.CTkFrame | None = None
        self._placeholder: ctk.CTkFrame | None = None
        self._clear_armed = False
        self._clear_timer: str | None = None
        #: Привязка колеса мыши к canvas'у (см. _enable_mousewheel).
        self._wheel_binding: tuple[tk.Canvas, str] | None = None

        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)  # растягивается список

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
        self._enable_mousewheel()

    # ------------------------------------------------------------------
    # прокрутка колесом мыши
    # ------------------------------------------------------------------

    def _find_canvas(self, widget: tk.Misc) -> tk.Canvas | None:
        """Ищет внутренний canvas прокручиваемой рамки по классу Tk."""
        if widget.winfo_class() == "Canvas":
            return widget  # type: ignore[return-value]
        for child in widget.winfo_children():
            found = self._find_canvas(child)
            if found is not None:
                return found
        return None

    def _enable_mousewheel(self) -> None:
        """Делает прокрутку колесом предсказуемой во всей вкладке.

        Штатная обработка ``CTkScrollableFrame`` имеет два недостатка:
        привязка идёт через ``bind_all`` (конфликтует с колесом других
        скроллируемых виджетов), а на Linux шаг прокрутки зависит от
        ``yscrollincrement`` — карточка выше шага не прокручивается вовсе,
        что выглядит как залипание. Здесь колесо обрабатывается точечно:
        привязка вешается только на сам список, один клик всегда даёт
        фиксированный шаг, а события над полями текста карточек
        перенаправляются списку.
        """
        canvas = self._find_canvas(self.scroll_frame)
        if canvas is None:
            logger.warning("не найден canvas истории — прокрутка колесом стандартная")
            return

        sequences = ("<MouseWheel>", "<Button-4>", "<Button-5>")

        def on_wheel(event: tk.Event) -> str:
            direction = _wheel_direction(event)
            if direction:
                canvas.yview_scroll(direction * WHEEL_SCROLL_LINES, "units")
            return "break"  # глобальный обработчик CTK больше не срабатывает

        for sequence in sequences:
            funcid = canvas.bind(sequence, on_wheel, add=True)
            self._wheel_binding = (canvas, funcid)

        # Поле текста карточки перехватывает колесо (прокручивает себя),
        # но у него всего 74px — реально оно не скроллит. Перенаправляем
        # его события списку; break внутри on_wheel не даёт CTK проскроллить
        # ещё и чужой виджет.
        self.scroll_frame.bind(
            "<Enter>",
            lambda _e: [
                w.bind(seq, on_wheel, add=True)
                for w in self.scroll_frame.winfo_children()
                if w.winfo_class() == "CTkTextbox"
                for seq in sequences
            ],
            add=True,
        )

    def disable_mousewheel(self) -> None:
        """Снимает привязку колеса (вызывается при закрытии приложения)."""
        binding = self._wheel_binding
        if binding is None:
            return
        canvas, _funcid = binding
        with contextlib.suppress(tk.TclError):
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                canvas.unbind(sequence)
        self._wheel_binding = None

    # ------------------------------------------------------------------
    # наполнение списка
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        """Блокирует кнопку очистки, если база недоступна."""
        self.btn_clear.configure(state="normal" if enabled else "disabled")

    def load(self, entries: Sequence[history.HistoryEntry]) -> None:
        """Заполняет список карточками; при пустом списке показывает заглушку."""
        for entry in entries:
            card = self.create_card(entry)
            card.pack(fill="x", padx=8, pady=(6, 0))
            if self._first is None:
                self._first = card
        if not entries:
            self.show_placeholder("История пуста")

    def prepend(self, entry: history.HistoryEntry) -> None:
        """Добавляет карточку в начало списка, без перезапуска приложения."""
        # Появились новые данные — подтверждение очистки снимаем, иначе оно
        # сработало бы уже над другим содержимым истории.
        self.cancel_clear()
        self.hide_placeholder()
        card = self.create_card(entry)
        first = self._first
        if first is not None and first.winfo_exists():
            card.pack(fill="x", padx=8, pady=(6, 0), before=first)
        else:
            card.pack(fill="x", padx=8, pady=(6, 0))
        self._first = card

    def clear_cards(self) -> None:
        """Убирает все карточки и заглушку из списка."""
        for widget in self.scroll_frame.pack_slaves():
            with contextlib.suppress(tk.TclError):
                widget.destroy()
        self._first = None
        self._placeholder = None

    def show_placeholder(self, text: str) -> None:
        """Показывает заглушку, пока записей нет или база недоступна."""
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
        """Убирает заглушку, когда появляется первая настоящая запись."""
        placeholder = self._placeholder
        if placeholder is None:
            return
        if self._first is placeholder:
            self._first = None
        self._placeholder = None
        with contextlib.suppress(tk.TclError):
            placeholder.destroy()

    # ------------------------------------------------------------------
    # карточка записи
    # ------------------------------------------------------------------

    def create_card(self, entry: history.HistoryEntry) -> ctk.CTkFrame:
        """
        Создаёт карточку одной записи истории.

        Карточка — дочерний элемент ``self.scroll_frame``, остальные
        виджеты — дочерние элементы карточки.
        """
        card = ctk.CTkFrame(self.scroll_frame, corner_radius=6)
        card.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text=history.format_local_time(entry.created_at),
            font=ctk.CTkFont(size=11),
            text_color=STATUS_GRAY,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        timer: dict[str, str | None] = {"after_id": None}
        button = ctk.CTkButton(
            header,
            text="Копировать 📋",
            width=150,
            height=26,
            font=ui_font(self._emoji_family, size=12),
            fg_color=COLOR_GREY,
            hover_color=COLOR_GREY_HOVER,
        )
        button.configure(command=lambda: self.copy_text(entry.text, button, timer))
        button.grid(row=0, column=1, sticky="e")

        text_box = ctk.CTkTextbox(card, height=74, wrap="word", font=ctk.CTkFont(size=13))
        # Вставляем до блокировки: в состоянии disabled вставка игнорируется.
        # Затем поле блокируется — стереть текст нельзя, а выделять мышкой
        # и копировать фрагменты можно.
        text_box.insert("1.0", entry.text)
        text_box.configure(state="disabled")
        text_box.grid(row=1, column=0, sticky="ew", padx=10, pady=(6, 10))
        return card

    def copy_text(self, text: str, button: ctk.CTkButton, timer: dict[str, str | None]) -> None:
        """
        Копирует текст записи в системный буфер обмена и показывает отклик.

        Кнопка на 1.5 секунды становится зелёной с надписью «Скопировано! ✅».
        Повторный клик перезапускает таймер, а не наслаивает второй.
        """
        try:
            button.clipboard_clear()
            button.clipboard_append(text)
            button.update_idletasks()
        except tk.TclError as exc:
            logger.warning("буфер обмена недоступен: %s", exc)
            return

        previous = timer.get("after_id")
        if previous:
            with contextlib.suppress(tk.TclError):
                button.after_cancel(previous)

        button.configure(
            text="Скопировано! ✅",
            fg_color=COLOR_GREEN,
            hover_color=COLOR_GREEN_HOVER,
        )

        def restore() -> None:
            timer["after_id"] = None
            try:
                if button.winfo_exists():
                    button.configure(
                        text="Копировать 📋",
                        fg_color=COLOR_GREY,
                        hover_color=COLOR_GREY_HOVER,
                    )
            except tk.TclError:
                pass  # карточку успели удалить

        timer["after_id"] = button.after(1500, restore)

    # ------------------------------------------------------------------
    # очистка с подтверждением
    # ------------------------------------------------------------------

    def on_clear_click(self) -> None:
        """
        Первое нажатие просит подтверждение, второе — очищает историю.

        Модальных окон в приложении нет, поэтому подтверждение встроено
        в саму кнопку: она краснеет и меняет надпись на пять секунд.
        """
        if not self._clear_armed:
            self.arm_clear()
            return
        self.cancel_clear()
        if self._on_cleared is not None:
            self._on_cleared()

    def arm_clear(self) -> None:
        """Взводит подтверждение очистки на ``CLEAR_CONFIRM_MS``."""
        self.cancel_clear()
        self._clear_armed = True
        self.btn_clear.configure(
            text=CLEAR_ARMED_LABEL,
            fg_color=COLOR_RED,
            hover_color=COLOR_RED_HOVER,
        )
        self._clear_timer = self.btn_clear.after(CLEAR_CONFIRM_MS, self.cancel_clear)

    def cancel_clear(self) -> None:
        """Снимает подтверждение и возвращает кнопке исходный вид."""
        if self._clear_timer is not None:
            with contextlib.suppress(tk.TclError):
                self.btn_clear.after_cancel(self._clear_timer)
            self._clear_timer = None
        if not self._clear_armed:
            return
        self._clear_armed = False
        with contextlib.suppress(tk.TclError):  # окно уже закрывается
            if self.btn_clear.winfo_exists():
                self.btn_clear.configure(
                    text=CLEAR_LABEL,
                    fg_color=COLOR_GREY,
                    hover_color=COLOR_GREY_HOVER,
                )
