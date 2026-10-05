"""
Элементы интерфейса, требующие живого виджета.

Всё, что можно проверить без графической среды — маркеры статуса, очистка
подписей и цвета пунктов — вынесено в :mod:`model_labels`. Здесь остаётся
то, что работает только с настоящим окном: подбор эмодзи-шрифта, покраска
живого меню и окно диспетчера моделей.

Сетевую логику модуль не дублирует, а импортирует из :mod:`download_manager`.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import tkinter as tk
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import customtkinter as ctk

import download_manager as dm
from model_labels import MENU_COLOR_DOWNLOADED, MENU_COLOR_MISSING

__all__ = [
    "ModelManagerWindow",
    "apply_menu_item_colors",
    "pick_emoji_font_family",
]

logger = logging.getLogger(__name__)

#: Палитра интерфейса. Приложение всегда работает в тёмной теме,
#: поэтому цвета заданы явно.
COLOR_RED = "#D32F2F"
COLOR_RED_HOVER = "#B71C1C"
COLOR_RED_ACTIVE = "#8E0000"
COLOR_GREY = "#3A3A3A"
COLOR_GREY_HOVER = "#4A4A4A"
COLOR_BLUE = "#1F6AA5"
COLOR_BLUE_HOVER = "#144870"
COLOR_ACCENT = "#3B8ED0"
COLOR_GREEN = "#2E7D32"
COLOR_GREEN_HOVER = "#1B5E20"

#: Через сколько миллисекунд снимается подтверждение удаления.
_DELETE_CONFIRM_MS = 5000

#: Чтобы предупреждение о приватном API не повторялось на каждый вызов.
_dropdown_warned = False


def pick_emoji_font_family() -> str | None:
    """
    Подбирает семейство шрифта, которое умеет рисовать цветные эмодзи.

    Без этого Tk берёт монохромный глиф из fallback-шрифта и 🟢 с 🔵
    превращаются в одинаковые серые кружки. Требует уже созданного
    корневого окна Tk: ``tkfont.families()`` без него не работает.

    Returns:
        Имя первого доступного семейства или ``None``, если ни одного нет.
    """
    candidates = ("Segoe UI Emoji", "Noto Color Emoji", "Apple Color Emoji")
    try:
        import tkinter.font as tkfont

        available = set(tkfont.families())
    except Exception:
        return None
    for family in candidates:
        if family in available:
            return family
    return None


def find_dropdown_menu(option_menu: Any) -> tk.Menu | None:
    """
    Достаёт внутреннее меню из ``CTkOptionMenu``.

    Атрибут ``_dropdown_menu`` приватный: если CustomTkinter его переименует,
    покраска пунктов молча перестанет работать — ровно та деградация, которую
    уже давали монохромные эмодзи. Поэтому отсутствие атрибута один раз
    попадает в журнал, а не проходит незамеченным.
    """
    global _dropdown_warned

    menu = getattr(option_menu, "_dropdown_menu", None)
    if menu is None and not _dropdown_warned:
        logger.warning(
            "у CTkOptionMenu нет атрибута _dropdown_menu — цвета пунктов "
            "меню работать не будут (изменился CustomTkinter?)"
        )
        _dropdown_warned = True
    return menu


def apply_menu_item_colors(
    option_menu: Any,
    models: Sequence[str],
    downloaded: Iterable[str],
) -> None:
    """
    Красит пункты выпадающего списка по отдельности.

    Зелёный — модель скачана, синий — отсутствует. Цветные эмодзи рисуются
    не везде (зависит от сборки Tk и доступного шрифта), поэтому статус
    дублируется цветом самого текста.

    ``DropdownMenu`` в CustomTkinter — наследник ``tkinter.Menu``, поэтому
    цвет задаётся через ``entryconfigure(foreground=...)``. Пункты
    пересобираются при каждом ``configure(values=...)``, так что метод нужно
    вызывать после каждого обновления списка.

    Args:
        option_menu: виджет ``CTkOptionMenu``.
        models: список моделей в том же порядке, что и подписи в меню.
        downloaded: множество доступных моделей.
    """
    menu = find_dropdown_menu(option_menu)
    if menu is None:
        return
    available = set(downloaded)
    for index, model in enumerate(models):
        color = MENU_COLOR_DOWNLOADED if model in available else MENU_COLOR_MISSING
        try:
            menu.entryconfigure(index, foreground=color)
        except tk.TclError:
            break  # в меню меньше пунктов, чем подписей


class ModelManagerWindow(ctk.CTkToplevel):
    """
    Окно диспетчера моделей: скачивание и удаление весов Whisper.

    Состояние доступных моделей окно не хранит у себя, а спрашивает у
    владельца через ``on_models_changed`` — так главное окно и диспетчер
    никогда не расходятся.

    Args:
        master: родительское окно.
        downloaded_models: множество доступных моделей на момент открытия.
        on_models_changed: перечитывает кэш и возвращает новое множество.
        on_busy_changed: уведомляет владельца о начале/конце скачивания,
            чтобы тот заблокировал свои кнопки.
        on_closed: вызывается при закрытии окна.
    """

    def __init__(
        self,
        master: Any,
        *,
        downloaded_models: Iterable[str],
        on_models_changed: Callable[[], set[str]],
        on_busy_changed: Callable[[bool], None],
        on_closed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._downloaded: set[str] = set(downloaded_models)
        self._on_models_changed = on_models_changed
        self._on_busy_changed = on_busy_changed
        self._on_closed = on_closed

        self._alive = True
        self._downloading: str | None = None
        self._cancel_requested = False
        self._pending_delete: str | None = None
        self._pending_delete_timer: str | None = None
        self._rows: dict[str, dict[str, Any]] = {}

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
            text=f"Файлы сохраняются в {dm.WHISPER_CACHE_DIR}",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).grid(row=1, column=0, sticky="w", padx=15, pady=(0, 10))

        self.list_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.list_frame.grid(row=2, column=0, sticky="ew", padx=15)
        self.list_frame.grid_columnconfigure(0, weight=1)

        for index, name in enumerate(dm.WHISPER_MODELS):
            self._build_row(index, name)

        self.progress_bar = ctk.CTkProgressBar(self, height=12)
        self.progress_bar.grid(row=3, column=0, sticky="ew", padx=15, pady=(14, 4))
        self.progress_bar.set(0)

        self.lbl_progress = ctk.CTkLabel(
            self,
            text="Выберите модель для загрузки",
            font=ctk.CTkFont(size=12),
            text_color="gray",
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

        # Окно должно оказаться поверх главного, а не за ним.
        self.update_idletasks()
        self._center_over_parent()
        self.after(10, self._bring_to_front)

    # --- построение строк ---

    def _build_row(self, index: int, name: str) -> None:
        """Создаёт строку списка: имя, размер, статус и кнопку действия."""
        row = ctk.CTkFrame(self.list_frame, corner_radius=6)
        row.grid(row=index, column=0, sticky="ew", pady=3)
        row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            row,
            text=name,
            width=70,
            anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=(12, 6), pady=8)

        size_label = ctk.CTkLabel(
            row,
            text="",
            width=90,
            anchor="w",
            text_color="gray",
            font=ctk.CTkFont(size=12),
        )
        size_label.grid(row=0, column=1, sticky="w", padx=(0, 10))

        status = ctk.CTkLabel(row, text="", width=130, anchor="e", font=ctk.CTkFont(size=12))
        status.grid(row=0, column=2, sticky="e", padx=(0, 10))

        button = ctk.CTkButton(
            row,
            text="Скачать",
            width=140,
            height=30,
            command=lambda n=name: self._on_row_action(n),
        )
        button.grid(row=0, column=3, sticky="e", padx=(0, 12), pady=6)

        self._rows[name] = {"size": size_label, "status": status, "button": button}

    def _refresh_all(self) -> None:
        """Перерисовывает состояние всех строк: размер, статус и действие."""
        self._downloaded = set(self._on_models_changed())
        for name in dm.WHISPER_MODELS:
            row = self._rows[name]
            downloaded = name in self._downloaded
            path = dm.model_file_path(name)

            # Размер: фактический, если файл уже лежит на диске.
            if path is not None and path.exists():
                try:
                    row["size"].configure(text=dm.format_bytes(path.stat().st_size))
                except OSError:
                    row["size"].configure(text=dm.WHISPER_MODEL_SIZES[name])
            else:
                row["size"].configure(text=dm.WHISPER_MODEL_SIZES[name])

            if self._downloading == name:
                row["status"].configure(text="Загрузка...", text_color=COLOR_ACCENT)
                row["button"].configure(
                    text="Скачивание...",
                    state="disabled",
                    fg_color=COLOR_GREY,
                    hover_color=COLOR_GREY,
                )
            elif downloaded:
                row["status"].configure(text="Готова к работе", text_color="green")
                if self._pending_delete == name:
                    row["button"].configure(
                        text="Точно удалить?",
                        state="normal",
                        fg_color=COLOR_RED,
                        hover_color=COLOR_RED_HOVER,
                    )
                else:
                    row["button"].configure(
                        text="Удалить",
                        state="normal",
                        fg_color=COLOR_GREY,
                        hover_color=COLOR_GREY_HOVER,
                    )
            else:
                row["status"].configure(text="Не скачана", text_color="gray")
                row["button"].configure(
                    text="Скачать",
                    state="disabled" if self._downloading else "normal",
                    fg_color=COLOR_BLUE,
                    hover_color=COLOR_BLUE_HOVER,
                )

    # --- вспомогательное ---

    def _center_over_parent(self) -> None:
        """Размещает окно по центру родителя."""
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
        """Поднимает окно поверх главного после отрисовки."""
        if not self._alive:
            return
        self.lift()
        self.focus_force()

    def _ui(self, callback: Callable[[], None]) -> None:
        """Планирует обновление виджетов в главном потоке Tkinter."""
        if not self._alive:
            return
        with contextlib.suppress(tk.TclError):  # окно уже уничтожено
            self.after(0, callback)

    def _set_message(self, text: str, color: str = "gray") -> None:
        """Показывает строку состояния под списком моделей."""
        self.lbl_progress.configure(text=text, text_color=color)

    # --- действия в строке ---

    def _on_row_action(self, name: str) -> None:
        """Скачать отсутствующую модель или удалить скачанную."""
        if self._downloading is not None:
            return  # во время загрузки строки заблокированы
        if name in self._downloaded:
            if self._pending_delete == name:
                self._cancel_pending_delete()
                self._delete_model(name)
            else:
                self._arm_pending_delete(name)
        else:
            self._start_download(name)

    def _arm_pending_delete(self, name: str) -> None:
        """Первое нажатие «Удалить»: подтверждение вторым нажатием."""
        self._cancel_pending_delete()
        self._pending_delete = name
        self._refresh_all()
        self._set_message(
            f"Нажмите «Точно удалить?» ещё раз, чтобы удалить веса '{name}'",
            COLOR_RED,
        )
        self._pending_delete_timer = self.after(_DELETE_CONFIRM_MS, self._cancel_pending_delete)

    def _cancel_pending_delete(self) -> None:
        """Снимает ожидание подтверждения удаления."""
        if self._pending_delete_timer is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._pending_delete_timer)
            self._pending_delete_timer = None
        if self._pending_delete is not None:
            self._pending_delete = None
            if self._alive:
                self._refresh_all()

    def _delete_model(self, name: str) -> None:
        """Удаляет файл весов (и недокачанный .part) из кэша."""
        path = dm.model_file_path(name)
        if path is None:
            self._set_message(f"❌ Не удалось определить файл модели '{name}'", COLOR_RED)
            return

        removed: list[str] = []
        freed = 0
        try:
            for target in (path, path.with_name(path.name + ".part")):
                if target.exists():
                    freed += target.stat().st_size
                    target.unlink()
                    removed.append(target.name)
        except OSError as exc:
            self._refresh_all()
            self._set_message(
                f"❌ Не удалось удалить {path.name}: {exc}. "
                f"Возможно, файл занят другой программой.",
                COLOR_RED,
            )
            return

        self._refresh_all()
        self.progress_bar.set(0)

        if not removed:
            self._set_message(f"Файлы модели '{name}' в кэше не найдены")
        elif name in self._downloaded:
            # Например, удалили large-v3.pt, но остался large-v2.pt.
            self._set_message(
                f"Удалён {', '.join(removed)} ({dm.format_bytes(freed)}), но модель "
                f"'{name}' всё ещё доступна — в кэше остались другие её веса.",
                "orange",
            )
        else:
            self._set_message(
                f"✓ Модель '{name}' удалена ({', '.join(removed)}), "
                f"освобождено {dm.format_bytes(freed)}",
                "green",
            )

    # --- загрузка ---

    def _start_download(self, name: str) -> None:
        """Запускает скачивание модели в отдельном потоке."""
        if self._downloading is not None:
            return
        self._cancel_pending_delete()

        url = dm.get_model_url(name)
        if not url:
            self._set_message(f"❌ Для модели '{name}' не найден адрес загрузки", COLOR_RED)
            return

        self._cancel_requested = False
        self._downloading = name
        self.progress_bar.set(0)
        self._set_message(f"Скачивание '{name}': подготовка...", COLOR_ACCENT)
        self._refresh_all()
        self._on_busy_changed(True)

        threading.Thread(target=self._download_worker, args=(name, url), daemon=True).start()

    def _download_worker(self, name: str, url: str) -> None:
        """Сеть и диск в отдельном потоке; виджеты не трогаются."""
        try:
            dm.download_whisper_model(
                url,
                dm.WHISPER_CACHE_DIR,
                on_progress=lambda done, total: self._ui(
                    lambda: self._show_progress(name, done, total)
                ),
                on_status=lambda text: self._ui(lambda: self._set_message(text, COLOR_ACCENT)),
                is_cancelled=lambda: self._cancel_requested,
            )
        except dm.DownloadCancelled:
            self._ui(lambda: self._after_cancel(name))
        except Exception as exc:
            # Имя exc удаляется по выходе из блока except, а лямбда выполнится
            # позже в главном потоке, поэтому фиксируем его как аргумент.
            self._ui(lambda error=exc: self._after_error(name, error))
        else:
            self._ui(lambda: self._after_success(name))

    def _show_progress(self, name: str, done: int, total: int) -> None:
        """Обновляет прогресс-бар и текст с процентами и объёмом."""
        if not self._alive or self._downloading != name:
            return
        if total > 0:
            fraction = min(done / total, 1.0)
            self.progress_bar.set(fraction)
            self._set_message(
                f"Скачивание '{name}': {fraction * 100:.0f}% "
                f"({dm.format_bytes(done)} из {dm.format_bytes(total)})",
                COLOR_ACCENT,
            )
        else:
            self.progress_bar.set(0)
            self._set_message(f"Скачивание '{name}': {dm.format_bytes(done)}", COLOR_ACCENT)

    def _after_success(self, name: str) -> None:
        """Скачивание завершено: обновляем и диспетчер, и главное окно."""
        # Признак загрузки снимается только здесь, в главном потоке. Если
        # сбросить его в рабочем потоке, между сбросом и этим обновлением
        # успеет стартовать другая загрузка — и её кнопки разблокируются.
        self._downloading = None
        if not self._alive:
            return
        self._refresh_all()
        self.progress_bar.set(1.0)
        self._set_message(f"✓ Модель '{name}' скачана и доступна для транскрибации", "green")
        self._on_busy_changed(False)

    def _after_error(self, name: str, exc: BaseException) -> None:
        """Сообщает об ошибке скачивания и возвращает окно в рабочее состояние."""
        self._downloading = None
        if not self._alive:
            return
        # Частично скачанный файл остаётся на диске при сетевом сбое,
        # поэтому обещать «файл удалён» можно не всегда.
        path = dm.model_file_path(name)
        part = path.with_name(path.name + ".part") if path else None
        resumable = part is not None and part.exists()
        self._refresh_all()
        self.progress_bar.set(0)
        tail = (
            "Повторите попытку — загрузка продолжится с места обрыва."
            if resumable
            else "Недокачанный файл удалён."
        )
        self._set_message(
            f"❌ Не удалось скачать '{name}': {dm.describe_download_error(exc)}. {tail}",
            COLOR_RED,
        )
        self._on_busy_changed(False)

    def _after_cancel(self, name: str) -> None:
        """Завершает отменённую загрузку."""
        self._downloading = None
        if not self._alive:
            return
        self._refresh_all()
        self.progress_bar.set(0)
        self._set_message(f"Загрузка '{name}' отменена, временный файл удалён")
        self._on_busy_changed(False)

    # --- закрытие ---

    def _on_close(self) -> None:
        """Закрывает окно; во время загрузки сначала останавливает поток."""
        if self._downloading is not None:
            self._cancel_requested = True
            self._set_message("Отмена загрузки...")
            self.after(100, self._wait_for_worker)
            return
        self._destroy()

    def _wait_for_worker(self) -> None:
        """Ждёт остановки потока загрузки, затем закрывает окно."""
        if not self._alive:
            return
        if self._downloading is not None:
            self.after(100, self._wait_for_worker)
            return
        self._destroy()

    def _destroy(self) -> None:
        """Снимает все флаги, уведомляет владельца и уничтожает окно."""
        self._alive = False
        self._cancel_pending_delete()
        self._on_busy_changed(False)
        if self._on_closed is not None:
            self._on_closed()
        self.destroy()
