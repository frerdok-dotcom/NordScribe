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

#: Высота шага прокрутки колесом как доля высоты видимой области списка.
#: Шаг считается от области, а не от высоты карточки: независимо от того,
#: короткие записи или длинные, одно движение колеса прокручивает
#: одинаковую часть экрана.
WHEEL_SCROLL_FRACTION = 0.15

#: Минимальный/максимальный шаг прокрутки в пикселях.
WHEEL_SCROLL_MIN_PX = 48
WHEEL_SCROLL_MAX_PX = 120

#: Максимум шагов, выполняемых в одном проходе цикла Tk. Ограничивает
#: «рывки» после лага: накопившиеся события не превращаются в прыжок
#: через весь список.
WHEEL_MAX_STEPS_PER_FLUSH = 1

#: Задержка перед общим проходом прокрутки (мс). События, пришедшие в одном
#: такте цикла Tk, складываются в один шаг, поэтому высокочастотные колёса
#: и трекпады не прокручивают список «через край».
WHEEL_FLUSH_MS = 0

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


def _destroyed(widget: tk.Misc) -> bool:
    """True, если виджет уже уничтожен (обращаться к нему нельзя)."""
    try:
        return not widget.winfo_exists()
    except tk.TclError:
        return True


def _is_descendant(widget: tk.Misc | None, container: tk.Misc) -> bool:
    """True, если ``widget`` — сам ``container`` или его потомок.

    Точнее проверки по координатам: виджеты могут перекрываться (кнопка
    «Очистить историю» лежит в той же области окна, что и список), поэтому
    «событие пришло со списка» определяется по дереву виджетов, а не по рамке.
    """
    current = widget
    while current is not None:
        if current is container:
            return True
        if _destroyed(current):
            return False
        try:
            current = current.master
        except tk.TclError:
            return False
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
        #: Виджеты со своей обработкой колеса: canvas списка, поля текста
        #: карточек и полоса прокрутки — см. :meth:`_enable_mousewheel`.
        self._wheel_bindings: list[tuple[tk.Misc, str]] = []
        #: Глобальные последовательности колеса, перехваченные вкладкой.
        self._wheel_all_sequences: tuple[str, ...] = ()
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

        Здесь своя механика, и опирается она на один общий обработчик,
        привязанный через ``bind_all`` **один раз** (``add=""``), а не на
        ``<Enter>``/``<Leave>`` рамки: у контейнера ``<Leave>`` срабатывает
        уже при входе курсора в дочерний виджет, поэтому «включать колесо по
        наведению» нельзя — при наведении на карточку или её текст привязка
        снималась, и список переставал прокручиваться.

        Обработчик активен всегда и сам решает, его ли это событие — по
        виджету, на котором оно возникло (``_is_descendant``):

        * событие пришло с виджета списка — прокручиваем список и возвращаем
          ``"break"``, поэтому штатный обработчик CTk (он тоже висит на
          ``bind_all``) до события не доберётся;
        * событие пришло с любого другого виджета (кнопка очистки, поле
          вывода на вкладке «Транскрибация») — выходим, не трогая событие.

        Шаг пропорционален высоте видимой области (~15 % экрана, но не
        меньше ``WHEEL_SCROLL_MIN_PX`` и не больше ``WHEEL_SCROLL_MAX_PX``),
        а позиция берётся из актуального ``yview`` — независимо от высоты
        карточек. События одного такта цикла Tk схлопываются в один шаг,
        поэтому высокочастотные колёса и трекпады не прокручивают список
        «через край».
        """
        canvas = self._find_scroll_canvas()
        if canvas is None:
            logger.warning("не найден canvas истории — прокрутка колесом стандартная")
            return

        sequences = WHEEL_SEQUENCES
        toplevel = canvas.winfo_toplevel()

        # Виджеты, чьё колесо прокручивает список: сама рамка списка и полоса
        # прокрутки (она лежит вне рамки, но относится к тому же списку).
        wheel_regions: list[tk.Misc] = [self.scroll_frame]

        def in_wheel_regions(widget: tk.Misc | None) -> bool:
            """True, если событие колеса пришло с виджета, который скроллит список."""
            return any(_is_descendant(widget, region) for region in wheel_regions)

        # Снимаем глобальную привязку штатных обработчиков CTkScrollableFrame:
        # без неё любое движение колеса в окне прокручивает этот список.
        for sequence in sequences:
            with contextlib.suppress(tk.TclError):
                toplevel.unbind_all(sequence)

        state: dict[str, int] = {"pending": 0, "flush_id": 0}

        def scroll_by(direction: int) -> None:
            """Прокручивает canvas на один шаг в сторону ``direction``."""
            # Актуальные размеры: scrollregion обновляется привязкой к
            # <Configure>, но после добавления карточек его лучше взять заново.
            with contextlib.suppress(tk.TclError):
                bbox = canvas.bbox("all")
                if not bbox or bbox[3] <= 0:
                    return  # содержимого ещё нет — прокручивать нечего
                height_px = float(bbox[3])
                top, bottom = canvas.yview()
                view = bottom - top
                if view >= 1.0:
                    return  # всё помещается на экране — прокручивать нечего

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
            """Отрабатывает накопленные движения колеса одним проходом."""
            state["flush_id"] = 0
            pending = state["pending"]
            state["pending"] = 0
            if not pending or _destroyed(canvas):
                return
            # Все события, накопившиеся за такт цикла Tk, дают ровно один шаг:
            # после серии быстрых движений (или подтормаживания) список не
            # «догоняет» прокрутку рывком через весь экран.
            direction = 1 if pending > 0 else -1
            for _ in range(min(abs(pending), WHEEL_MAX_STEPS_PER_FLUSH)):
                scroll_by(direction)

        def schedule_flush() -> None:
            if not state["flush_id"]:
                state["flush_id"] = canvas.after(WHEEL_FLUSH_MS, flush)

        def on_wheel(event: tk.Event) -> str | None:
            # Привязка идёт через bind_all, поэтому событие приходит с любого
            # виджета окна: свою прокрутку выполняем только для виджетов
            # списка (карточка, её текстовое поле, полоса прокрутки, зазоры).
            if not in_wheel_regions(getattr(event, "widget", None)):
                return None

            direction = _wheel_direction(event)
            if not direction:
                return "break"

            # Направления складываются: +1 и -1 в одном такте гасят друг
            # друга (дёрнули колесо туда-обратно — список стоит на месте).
            state["pending"] += direction
            schedule_flush()
            return "break"  # другие обработчики этого события не срабатывают

        for sequence in sequences:
            self._wheel_bindings.append((canvas, canvas.bind(sequence, on_wheel, add="")))

        # Поля текста карточек перехватывают колесо (прокручивают себя),
        # но реально не скроллят — перенаправляем их события списку.
        self._rebind_wheel = lambda: self._rebind_tree(canvas, on_wheel)
        self._rebind_wheel()

        # Полоса прокрутки CTk перехватывает колесо у себя и скроллит
        # «единицами» canvas (на Windows это 1 px за тик — почти стоячая
        # прокрутка). Перенаправляем её события на общий обработчик.
        scrollbar = getattr(self.scroll_frame, "_scrollbar", None)
        sb_canvas = getattr(scrollbar, "_canvas", None)
        if isinstance(sb_canvas, tk.Canvas):
            wheel_regions.append(sb_canvas)
            for sequence in sequences:
                bound = sb_canvas.bind(sequence, on_wheel, add="")
                self._wheel_bindings.append((sb_canvas, bound))

        # Полоса прокрутки тоже ходит «единицами» canvas: на Windows это 1 px
        # за шаг, поэтому ползунок двигался почти незаметно. Задаём видимый шаг.
        self._configure_scroll_increment()
        canvas.bind("<Configure>", self._on_canvas_configure, add="+")

        # Глобальная привязка: колесо работает над любой точкой истории
        # (включая полосу прокрутки, зазоры и дочерние фреймы карточек).
        # Обработчик сам проверяет, с какого виджета пришло событие, поэтому
        # над остальными виджетами окна он молчит и событие уходит дальше.
        self._wheel_all_sequences = sequences
        for sequence in sequences:
            toplevel.bind_all(sequence, on_wheel, add="")

    def _on_canvas_configure(self, _event: tk.Event) -> None:
        """Пересчитывает шаг полосы прокрутки при изменении размеров списка."""
        self._configure_scroll_increment()

    def _configure_scroll_increment(self) -> None:
        """Задаёт шаг полосы прокрутки в пикселях (по умолчанию на Windows 1 px)."""
        canvas = self._find_scroll_canvas()
        if canvas is None or _destroyed(canvas):
            return
        with contextlib.suppress(tk.TclError):
            viewport_px = max(canvas.winfo_height(), WHEEL_SCROLL_MIN_PX)
            step_px = int(min(max(viewport_px * 0.05, 8), 40))
            if int(canvas.cget("yscrollincrement") or 0) != step_px:
                canvas.configure(yscrollincrement=step_px)

    def disable_mousewheel(self) -> None:
        """Снимает привязки колеса (вызывается при закрытии приложения)."""
        for widget, _funcid in self._wheel_bindings:
            if _destroyed(widget):
                continue
            with contextlib.suppress(tk.TclError):
                for sequence in WHEEL_SEQUENCES:
                    widget.unbind(sequence)
        self._wheel_bindings = []
        self._rebind_wheel = lambda: None

        if self._wheel_all_sequences:
            canvas = self._find_scroll_canvas()
            if canvas is not None and not _destroyed(canvas):
                with contextlib.suppress(tk.TclError):
                    toplevel = canvas.winfo_toplevel()
                    for sequence in self._wheel_all_sequences:
                        toplevel.unbind_all(sequence)
            self._wheel_all_sequences = ()

    def _rebind_tree(
        self,
        canvas: tk.Canvas,
        handler: Callable[[tk.Event], str | None],
    ) -> None:
        """Навешивает обработчик колеса на поля текста карточек списка.

        Вызывается после пересоздания карточек (``load``/``prepend``), поэтому
        сначала убирает привязки с уничтоженных виджетов — иначе Tk вызывает
        обработчик уже мёртвого виджета. Вызов идемпотентен.
        """
        canvas_path = str(canvas)
        alive: list[tuple[tk.Misc, str]] = []
        for widget, funcid in self._wheel_bindings:
            if _destroyed(widget):
                continue
            # Привязки на потомках canvas пересоздаются ниже.
            if widget is not canvas and str(widget).startswith(canvas_path):
                with contextlib.suppress(tk.TclError):
                    for sequence in WHEEL_SEQUENCES:
                        widget.unbind(sequence)
                continue
            alive.append((widget, funcid))
        self._wheel_bindings = alive

        for child in canvas.winfo_children():
            self._rebind_widget_tree(child, handler)

    def _rebind_widget_tree(
        self,
        widget: tk.Misc,
        handler: Callable[[tk.Event], str | None],
    ) -> None:
        """Вешает обработчик колеса на текстовые поля внутри карточки."""
        if widget.winfo_class() in ("CTkTextbox", "Text"):
            for sequence in WHEEL_SEQUENCES:
                self._wheel_bindings.append((widget, widget.bind(sequence, handler, add="")))
        for child in widget.winfo_children():
            self._rebind_widget_tree(child, handler)

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
        # У уничтоженных карточек остались привязки колеса: снимаем их, иначе
        # Tk будет звать обработчик виджета, которого уже нет.
        self._rebind_wheel()

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
