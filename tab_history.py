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

#: Высота шага прокрутки колесом в пикселях (независимо от высоты карточек).
#: Подбирается автоматически по высоте видимой области списка.
WHEEL_SCROLL_FRACTION = 0.30

#: Минимальный/максимальный шаг прокрутки в пикселях.
WHEEL_SCROLL_MIN_PX = 60
WHEEL_SCROLL_MAX_PX = 220

#: Максимум шагов прокрутки из очереди за один проход цикла Tk.
#: Ограничивает «рывки» после лага: накопившиеся события отрабатываются
#: не целиком, а частями — список идёт плавно и предсказуемо.
WHEEL_MAX_STEPS_PER_FLUSH = 1

#: Последовательности событий колеса мыши, различающиеся по платформам.
WHEEL_SEQUENCES = ("<MouseWheel>", "<Button-4>", "<Button-5>")


def _wheel_direction(event: tk.Event) -> int:
    """Возвращает направление прокрутки: -1 — вверх, +1 — вниз, 0 — нет события.

    Учитывает различия платформ: Windows/macOS присылают ``delta``,
    Linux/X11 — виртуальные кнопки Button-4/5. Величина дельты не важна:
    одно движение колеса = один фиксированный шаг, иначе высокочастотные
    колёса и трекпады прокручивают список «через край».
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


def _event_inside(widget: tk.Misc, event: tk.Event) -> bool:
    """True, если курсор события находится внутри виджета (с потомками).

    Привязка через ``bind_all`` приходит на все виджеты окна, поэтому перед
    прокруткой проверяем координаты: иначе колесо над любой кнопкой
    прокручивало бы историю.
    """
    try:
        wx = widget.winfo_rootx()
        wy = widget.winfo_rooty()
        return (
            wx <= event.x_root < wx + widget.winfo_width()
            and wy <= event.y_root < wy + widget.winfo_height()
        )
    except tk.TclError:
        return False


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
        #: Дочерние виджеты, чьё колесо перенаправлено списку.
        self._wheel_rebound: list[tk.Misc] = []
        #: Обновить привязки колеса после пересоздания карточек.
        self._rebind_wheel: Callable[[], None] = lambda: None

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

    def _find_scroll_canvas(self) -> tk.Canvas | None:
        """Возвращает внутренний canvas прокручиваемой рамки.

        В разных версиях ``customtkinter`` полотно называется по-разному
        (``_parent_canvas`` / ``_canvas``), поэтому перебираем известные
        имена; запасной путь — обход дерева виджетов по классу Tk.
        """
        for attr in ("_parent_canvas", "_canvas"):
            canvas = getattr(self.scroll_frame, attr, None)
            if isinstance(canvas, tk.Canvas):
                return canvas
        return self._find_canvas(self.scroll_frame)

    def _enable_mousewheel(self) -> None:
        """Делает прокрутку колесом предсказуемой во всей вкладке.

        Штатная обработка ``CTkScrollableFrame`` — главный источник жалоб
        «залипает / глючит»:

        * привязка идёт через ``bind_all``, поэтому история прокручивается
          от колеса в ЛЮБОМ месте окна (даже над кнопками и полем вывода
          другой вкладки), а несколько скроллируемых областей дергают друг
          друга одновременно;
        * шаг «единицами» зависит от ``yscrollincrement`` (Windows: 1 px,
          Linux: 30 px на тик) — колесо то еле ползёт, то перескакивает
          полэкрана;
        * события над полем текста карточки перехватывает само текстовое
          поле, которое почти ничего не скроллит — курсор на тексте, колесо
          крутится, список стоит.

        Здесь своя механика:
        * фиксированный шаг пропорционален высоте видимой области (~30 %
          экрана, но не меньше ``WHEEL_SCROLL_MIN_PX`` и не больше
          ``WHEEL_SCROLL_MAX_PX``);
        * очередь направлений вместо дебаунса по времени: каждое движение
          колеса даёт ровно один шаг, инерционные хвосты трекпада
          схлопываются, а при резкой смене направления противоположные
          шаги компенсируют друг друга;
        * прокрутка выполняется батчем из цикла Tk (не более
          ``WHEEL_MAX_STEPS_PER_FLUSH`` шагов за проход) — после лага
          накопленные события не превращаются в рывок через весь список;
        * актуальный ``scrollregion`` пересчитывается перед каждым шагом.
        Глобальная привязка CTK снимается и возвращается только на время,
        пока курсор находится над списком истории.
        """
        canvas = self._find_scroll_canvas()
        if canvas is None:
            logger.warning("не найден canvas истории — прокрутка колесом стандартная")
            return

        sequences = WHEEL_SEQUENCES

        # Снимаем глобальную привязку штатных обработчиков CTkScrollableFrame:
        # без неё любое движение колеса в окне прокручивает этот список.
        toplevel = canvas.winfo_toplevel()
        for sequence in sequences:
            with contextlib.suppress(tk.TclError):
                toplevel.unbind_all(sequence)

        state = {"queue": [], "flush_id": None, "scheduled": False}

        def scroll_one(direction: int) -> None:
            """Прокручивает canvas на один шаг в сторону ``direction``."""
            # Актуальные размеры: scrollregion обновляется привязкой к
            # <Configure>, но после добавления карточек его лучше взять заново.
            with contextlib.suppress(tk.TclError):
                canvas.update_idletasks()
                bbox = canvas.bbox("all")
                if not bbox or bbox[3] <= 0:
                    return  # содержимого ещё нет — прокручивать нечего
                height_px = bbox[3]
                top, bottom = canvas.yview()
                view = bottom - top
                if view >= 1.0:
                    return  # всё помещается на экране — прокручивать нечего

                viewport_px = max(canvas.winfo_height(), 1)
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
            """Обрабатывает накопленные шаги (с компенсацией направлений)."""
            state["flush_id"] = None
            queue = state["queue"]
            if not queue:
                state["scheduled"] = False
                return
            # Направления складываются: +1 и -1 гасят друг друга. За один
            # проход — не больше WHEEL_MAX_STEPS_PER_FLUSH шагов; остаток
            # очереди переносится на следующий, чтобы после лага список
            # не «летел» через весь контент одним рывком.
            net = sum(queue)
            steps = abs(net)
            direction = 1 if net > 0 else -1
            done = min(steps, WHEEL_MAX_STEPS_PER_FLUSH)
            for _ in range(done):
                scroll_one(direction)
            leftover = steps - done
            if leftover:
                state["queue"] = [direction] * leftover
                schedule_flush()
            else:
                state["queue"] = []
                state["scheduled"] = False

        def schedule_flush() -> None:
            if not state["scheduled"]:
                state["scheduled"] = True
                state["flush_id"] = canvas.after(16, flush)

        def cancel_pending() -> None:
            flush_id = state["flush_id"]
            if flush_id is not None:
                with contextlib.suppress(tk.TclError):
                    canvas.after_cancel(flush_id)
                state["flush_id"] = None
            state["queue"] = []
            state["scheduled"] = False

        def on_wheel(event: tk.Event) -> str | None:
            # При связке bind_all событие приходит от любого виджета окна;
            # реагируем, только если курсор над списком истории.
            if not _event_inside(self.scroll_frame, event):
                return None

            direction = _wheel_direction(event)
            if not direction:
                return "break"

            # Простая очередь направлений: +1 и -1 гасят друг друга при
            # флэтере, каждое не скомпенсированное движение колеса даёт
            # ровно один фиксированный шаг прокрутки.
            state["queue"].append(direction)
            schedule_flush()
            return "break"  # другие обработчики этого события не срабатывают

        for sequence in sequences:
            funcid = canvas.bind(sequence, on_wheel, add="")
            self._wheel_binding = (canvas, funcid)

        # Поля текста карточек перехватывают колесо (прокручивают себя),
        # но реально не скроллят — перенаправляем их события списку.
        self._wheel_rebound = []

        def rebind_children(_event: object = None) -> None:
            """Вешает перенаправление колеса на поля текста карточек."""
            for widget in canvas.winfo_children():
                self._rebind_tree(widget, sequences, on_wheel)

        self._rebind_wheel = rebind_children
        rebind_children()

        # Глобальная привязка активна, только пока курсор внутри списка:
        # так колесо работает над любой точкой истории (включая полосу
        # прокрутки, зазоры и дочерние фреймы карточек), но молчит, когда
        # курсор ушёл на другие вкладки и виджеты.
        def enter(_event: tk.Event) -> None:
            for sequence in sequences:
                toplevel.bind_all(sequence, on_wheel, add="")

        def leave(_event: tk.Event) -> None:
            cancel_pending()
            for sequence in sequences:
                toplevel.unbind_all(sequence)

        self.scroll_frame.bind("<Enter>", enter, add="")
        self.scroll_frame.bind("<Leave>", leave, add="")

        # Полоса прокрутки CTk перехватывает колесо у себя и скроллит
        # «единицами» canvas (на Windows это 1 px за тик — почти стоячая
        # прокрутка). Перенаправляем её события на общий обработчик.
        scrollbar = getattr(self.scroll_frame, "_scrollbar", None)
        sb_canvas = getattr(scrollbar, "_canvas", None)
        if isinstance(sb_canvas, tk.Canvas):
            for sequence in sequences:
                funcid = sb_canvas.bind(sequence, on_wheel, add="")
                self._wheel_rebound.append(sb_canvas)

    def disable_mousewheel(self) -> None:
        """Снимает привязку колеса (вызывается при закрытии приложения)."""
        binding = self._wheel_binding
        if binding is not None:
            canvas, _funcid = binding
            with contextlib.suppress(tk.TclError):
                toplevel = canvas.winfo_toplevel()
                for sequence in WHEEL_SEQUENCES:
                    canvas.unbind(sequence)
                    toplevel.unbind_all(sequence)
            self._wheel_binding = None
        for widget in getattr(self, "_wheel_rebound", []):
            with contextlib.suppress(tk.TclError):
                for sequence in WHEEL_SEQUENCES:
                    widget.unbind(sequence)
        self._wheel_rebound = []

    def _rebind_tree(
        self,
        widget: tk.Misc,
        sequences: tuple[str, ...],
        handler: Callable[[tk.Event], str | None],
    ) -> None:
        """Рекурсивно перенаправляет события колеса дочерних виджетов списку."""
        if widget.winfo_class() in ("CTkTextbox", "Text"):
            for sequence in sequences:
                widget.bind(sequence, handler, add="")
            self._wheel_rebound.append(widget)
        for child in widget.winfo_children():
            self._rebind_tree(child, sequences, handler)

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
        self._rebind_wheel()

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
        self._rebind_wheel()

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
