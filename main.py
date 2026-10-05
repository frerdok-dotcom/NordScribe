"""
NordScribe — локальная транскрибация аудио через OpenAI Whisper.

Точка входа и главное окно. Модуль намеренно тонкий: он только связывает
виджеты с бэкендом.

* сетевая логика и работа с файлами моделей — :mod:`download_manager`;
* запись звука и распознавание — :mod:`whisper_worker`;
* чистые подписи моделей — :mod:`model_labels`;
* окно диспетчера и покраска меню — :mod:`gui_components`;
* настройки и журнал — :mod:`settings`;
* проверки окружения — :mod:`preflight`.

Запуск::

    python main.py
"""

from __future__ import annotations

import contextlib
import logging
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

import download_manager as dm
import gui_components as gui
import history
import model_labels as labels
import preflight
import settings as app_settings
import whisper_worker as ww

__all__ = ["NordScribeApp", "main"]

logger = logging.getLogger(__name__)

#: Допустимая строка геометрии окна вида 1100x700 или 1100x700+100+50.
_GEOMETRY_RE = re.compile(r"^\d+x\d+([+-]\d+[+-]\d+)?$")

#: Сколько ждать подтверждения очистки истории.
_HISTORY_CLEAR_CONFIRM_MS = 5000


class NordScribeApp(ctk.CTk):
    """
    Главное окно приложения.

    Тяжёлые операции (загрузка весов Whisper, распознавание) выполняются
    в :class:`whisper_worker.WhisperWorker`, поэтому нажатие любой кнопки
    не блокирует интерфейс. Все обновления виджетов из рабочих потоков
    проходят через ``after(0, ...)``.
    """

    SAMPLE_RATE = 16000

    def __init__(self, settings: app_settings.Settings | None = None) -> None:
        super().__init__()

        self.settings = settings or app_settings.Settings()
        self.title("NordScribe - Локальная Транскрибация")
        self._apply_saved_geometry()

        # --- состояние моделей ---
        self.downloaded_whisper_models: set[str] = dm.get_downloaded_whisper_models()
        selected = (
            self.settings.model
            if self.settings.model in dm.WHISPER_MODELS
            else dm.WHISPER_MODELS[0]
        )
        self.whisper_display_options: list[str] = labels.build_model_labels(
            dm.WHISPER_MODELS, self.downloaded_whisper_models
        )
        # Две РАЗНЫЕ переменные, и это принципиально:
        #   whisper_var         — чистое имя модели ("large"), идёт в load_model;
        #   whisper_display_var — подпись для виджета ("🟢 large").
        # CTkOptionMenu показывает текст, привязанный к своей variable, поэтому
        # если хранить в ней чистое имя, маркер в свёрнутом виде не виден.
        self.whisper_var = ctk.StringVar(value=selected)
        self.whisper_display_var = ctk.StringVar(
            value=labels.label_for_model(self.whisper_display_options, selected)
        )

        # --- фоновые задачи ---
        self._download_active = False
        self._transcription_active = False
        self._pending_temp: Path | None = None
        self.model_manager: gui.ModelManagerWindow | None = None

        self.recorder = ww.AudioRecorder(
            sample_rate=self.SAMPLE_RATE,
            on_tick=self._on_recording_tick,
            trim_silence=self.settings.trim_silence,
        )
        self.worker = ww.WhisperWorker(
            model_name_provider=self._selected_model_name,
            language=self.settings.language,
        )

        # Семейство шрифта с цветными эмодзи: иначе маркеры и значки
        # на кнопках рисуются монохромными квадратами.
        self._emoji_family = gui.pick_emoji_font_family()

        # История транскрибаций: своя база в каталоге данных приложения
        # (~/.nordscribe/history.db), а не индекс CodeGraph из .codegraph/.
        self.history = history.TranscriptionHistory()
        self._history_available = self.history.init_schema()
        self._history_first: ctk.CTkFrame | None = None
        self._history_placeholder: ctk.CTkFrame | None = None
        self._history_clear_armed = False
        self._history_clear_timer: str | None = None

        self._build_layout()
        gui.apply_menu_item_colors(
            self.whisper_menu, dm.WHISPER_MODELS, self.downloaded_whisper_models
        )
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._run_preflight()
        self._load_history()

    # ------------------------------------------------------------------
    # запуск и завершение
    # ------------------------------------------------------------------

    def _apply_saved_geometry(self) -> None:
        """Восстанавливает размер окна, если сохранённое значение корректно."""
        geometry = (self.settings.window_geometry or "").strip()
        if _GEOMETRY_RE.match(geometry):
            self.geometry(geometry)
        else:
            logger.debug("геометрия %r не распознана, беру размер по умолчанию", geometry)
            self.geometry("1100x700")

    def _run_preflight(self) -> None:
        """Проверяет окружение и сообщает о проблемах в строке статуса."""
        try:
            results = preflight.run_checks()
        except Exception:
            logger.exception("проверки окружения не выполнены")
            return
        summary = preflight.summarize(results)
        if summary:
            self.lbl_status.configure(text=summary, text_color="orange")
        logger.info(
            "окружение: %s",
            "; ".join(f"{r.name}={'ok' if r.ok else 'нет'}" for r in results),
        )

    def destroy(self) -> None:
        """Сохраняет настройки, останавливает запись и глушит пул задач."""
        try:
            self._shutdown()
        except Exception:
            logger.exception("ошибка при завершении работы")
        finally:
            super().destroy()

    def _shutdown(self) -> None:
        """
        Освобождает ресурсы перед выходом.

        Раньше окно закрывалось, оставив открытым поток PortAudio и болтающийся
        временный WAV: запись не останавливалась, а файл никто не удалял.
        """
        self._cancel_history_clear()
        if self.recorder.is_recording:
            try:
                ww.cleanup_temp_file(self.recorder.stop())
            except ww.RecorderError as exc:
                logger.warning("не удалось корректно остановить запись: %s", exc)
        if ww.cleanup_temp_file(self._pending_temp):
            logger.info("удалён незавершённый временный файл")
        self._pending_temp = None

        self.settings.update(
            model=self._selected_model_name(),
            window_geometry=self.geometry(),
        )
        self.settings.save()

        self.worker.shutdown(wait=False)
        logger.info("приложение остановлено")

    # ------------------------------------------------------------------
    # построение интерфейса
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        """Создаёт все виджеты главного окна."""
        # Одна колонка во всю ширину: панель настроек и область вкладок.
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        self._build_settings_row()
        self._build_tabbed_area()

    def _build_settings_row(self) -> None:
        """Верхняя панель: выбор модели и кнопка диспетчера."""
        self.settings_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.settings_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 5))
        self.settings_frame.grid_columnconfigure(0, weight=0)
        self.settings_frame.grid_columnconfigure(1, weight=0)
        self.settings_frame.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(
            self.settings_frame,
            text="Модель Whisper:",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))

        def menu_font() -> ctk.CTkFont:
            return self._ui_font(size=13)

        self.whisper_menu = ctk.CTkOptionMenu(
            self.settings_frame,
            variable=self.whisper_display_var,
            values=self.whisper_display_options,
            command=self._on_whisper_model_change,
            width=140,
            height=30,
            font=menu_font(),
            dropdown_font=menu_font(),
        )
        self.whisper_menu.grid(row=0, column=1, sticky="w")

        self.btn_manage_models = ctk.CTkButton(
            self.settings_frame,
            text="⚙ Управление моделями",
            command=self.open_model_manager,
            width=190,
            height=30,
            fg_color=gui.COLOR_GREY,
            hover_color=gui.COLOR_GREY_HOVER,
        )
        self.btn_manage_models.grid(row=0, column=2, sticky="e")

    def _ui_font(self, size: int = 13, bold: bool = False) -> ctk.CTkFont:
        """
        Шрифт интерфейса.

        Если в системе есть шрифт с цветными эмодзи, он подставляется — иначе
        значки вроде 📋 рисуются пустым квадратом (проверено снимком экрана).
        """
        weight = "bold" if bold else "normal"
        if self._emoji_family:
            return ctk.CTkFont(family=self._emoji_family, size=size, weight=weight)
        return ctk.CTkFont(size=size, weight=weight)

    def _build_tabbed_area(self) -> None:
        """
        Делит основную область на вкладки — всё остаётся в одном окне.

        CTkTabview растягивает активную вкладку (внутри он ставит её с
        sticky="nsew" в строку с weight=1), поэтому поле транскрибации
        занимает всю высоту, как и до появления истории.
        """
        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=15, pady=(0, 15))
        self.tab_transcription = self.tabview.add("Транскрибация")
        self.tab_history = self.tabview.add("История")
        self._build_transcription_tab()
        self._build_history_tab()

    def _build_transcription_tab(self) -> None:
        """Заголовок, поле вывода, кнопки управления и индикатор прогресса."""
        tab = self.tab_transcription
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)  # растягивается поле вывода

        self.lbl_transcript = ctk.CTkLabel(
            tab,
            text="🎙️ Транскрибация (Whisper)",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        self.lbl_transcript.grid(row=0, column=0, sticky="nw", pady=(4, 10))

        self.txt_transcript = ctk.CTkTextbox(tab, wrap="word", font=ctk.CTkFont(size=14))
        self.txt_transcript.grid(row=1, column=0, sticky="nsew", pady=(0, 10))

        self.frame_controls = ctk.CTkFrame(tab, fg_color="transparent")
        self.frame_controls.grid(row=2, column=0, sticky="ew")
        self.frame_controls.grid_columnconfigure((0, 1), weight=1)

        self.btn_load_audio = ctk.CTkButton(
            self.frame_controls,
            text="📁 Загрузить аудиофайл",
            command=self.start_transcription_thread,
            height=45,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.btn_load_audio.grid(row=0, column=0, padx=(0, 10), pady=(0, 10), sticky="ew")

        self.btn_record_mic = ctk.CTkButton(
            self.frame_controls,
            text="🎤 Записать с микрофона",
            command=self.toggle_recording,
            fg_color=gui.COLOR_RED,
            hover_color=gui.COLOR_RED_HOVER,
            height=45,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.btn_record_mic.grid(row=0, column=1, padx=(10, 0), pady=(0, 10), sticky="ew")

        self.progress_bar = ctk.CTkProgressBar(self.frame_controls, height=10)
        self.progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 10))
        self.progress_bar.set(0)

        self.lbl_status = ctk.CTkLabel(
            self.frame_controls,
            text="Готов к работе",
            text_color="gray",
            font=ctk.CTkFont(size=13),
        )
        self.lbl_status.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 5))

    # ------------------------------------------------------------------
    # история транскрибаций
    # ------------------------------------------------------------------

    def _build_history_tab(self) -> None:
        """Кнопка очистки и прокручиваемый список прошлых транскрибаций."""
        tab = self.tab_history
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)  # растягивается список

        header = ctk.CTkFrame(tab, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        header.grid_columnconfigure(0, weight=1)

        self.btn_clear_history = ctk.CTkButton(
            header,
            text="Очистить историю 🗑",
            width=190,
            height=28,
            font=self._ui_font(size=12),
            fg_color=gui.COLOR_GREY,
            hover_color=gui.COLOR_GREY_HOVER,
            command=self._on_clear_history_click,
        )
        self.btn_clear_history.grid(row=0, column=1, sticky="e")
        if not self._history_available:
            self.btn_clear_history.configure(state="disabled")

        self.history_scroll_frame = ctk.CTkScrollableFrame(tab)
        self.history_scroll_frame.grid(row=1, column=0, sticky="nsew")

    def _load_history(self) -> None:
        """Заполняет вкладку истории записями из базы при старте приложения."""
        if not self._history_available:
            if self.lbl_status.cget("text") == "Готов к работе":
                self.lbl_status.configure(
                    text="История недоступна: не удалось открыть базу данных",
                    text_color="orange",
                )
            self._show_history_placeholder("История недоступна")
            return

        entries = self.history.all()
        for entry in entries:
            card = self._create_history_card(entry)
            card.pack(fill="x", padx=8, pady=(6, 0))
            if self._history_first is None:
                self._history_first = card
        if not entries:
            self._show_history_placeholder("История пуста")
        logger.info("история: загружено записей — %d", len(entries))

    def _show_history_placeholder(self, text: str) -> None:
        """Показывает заглушку, пока записей нет или база недоступна."""
        placeholder = ctk.CTkFrame(self.history_scroll_frame, fg_color="transparent")
        placeholder.pack(fill="x", padx=8, pady=20)
        ctk.CTkLabel(
            placeholder,
            text=text,
            text_color="gray",
            font=ctk.CTkFont(size=13),
        ).pack()
        self._history_placeholder = placeholder
        if self._history_first is None:
            self._history_first = placeholder

    def _hide_history_placeholder(self) -> None:
        """Убирает заглушку, когда появляется первая настоящая запись."""
        placeholder = self._history_placeholder
        if placeholder is None:
            return
        if self._history_first is placeholder:
            self._history_first = None
        self._history_placeholder = None
        with contextlib.suppress(tk.TclError):
            placeholder.destroy()

    def _create_history_card(self, entry: history.HistoryEntry) -> ctk.CTkFrame:
        """
        Создаёт карточку одной записи истории.

        Карточка — дочерний элемент ``self.history_scroll_frame``, остальные
        виджеты — дочерние элементы карточки.
        """
        card = ctk.CTkFrame(self.history_scroll_frame, corner_radius=6)
        card.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text=history.format_local_time(entry.created_at),
            font=ctk.CTkFont(size=11),
            text_color="gray",
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        timer: dict[str, str | None] = {"after_id": None}
        button = ctk.CTkButton(
            header,
            text="Копировать 📋",
            width=150,
            height=26,
            font=self._ui_font(size=12),
            fg_color=gui.COLOR_GREY,
            hover_color=gui.COLOR_GREY_HOVER,
        )
        button.configure(command=lambda: self._copy_history_text(entry.text, button, timer))
        button.grid(row=0, column=1, sticky="e")

        text_box = ctk.CTkTextbox(card, height=74, wrap="word", font=ctk.CTkFont(size=13))
        # Вставляем до блокировки: в состоянии disabled вставка игнорируется.
        # Затем поле блокируется — стереть текст нельзя, а выделять мышкой
        # и копировать фрагменты можно.
        text_box.insert("1.0", entry.text)
        text_box.configure(state="disabled")
        text_box.grid(row=1, column=0, sticky="ew", padx=10, pady=(6, 10))
        return card

    def _prepend_history_card(self, entry: history.HistoryEntry) -> None:
        """Добавляет карточку в начало списка, без перезапуска приложения."""
        # Появились новые данные — подтверждение очистки снимаем, иначе оно
        # сработало бы уже над другим содержимым истории.
        self._cancel_history_clear()
        self._hide_history_placeholder()
        card = self._create_history_card(entry)
        first = self._history_first
        if first is not None and first.winfo_exists():
            card.pack(fill="x", padx=8, pady=(6, 0), before=first)
        else:
            card.pack(fill="x", padx=8, pady=(6, 0))
        self._history_first = card

    def _copy_history_text(
        self, text: str, button: ctk.CTkButton, timer: dict[str, str | None]
    ) -> None:
        """
        Копирует текст записи в системный буфер обмена и показывает отклик.

        Кнопка на 1.5 секунды становится зелёной с надписью «Скопировано! ✅».
        Повторный клик перезапускает таймер, а не наслаивает второй.
        """
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
        except tk.TclError as exc:
            logger.warning("буфер обмена недоступен: %s", exc)
            return

        previous = timer.get("after_id")
        if previous:
            with contextlib.suppress(tk.TclError):
                button.after_cancel(previous)

        button.configure(
            text="Скопировано! ✅",
            fg_color=gui.COLOR_GREEN,
            hover_color=gui.COLOR_GREEN_HOVER,
        )

        def restore() -> None:
            timer["after_id"] = None
            try:
                if button.winfo_exists():
                    button.configure(
                        text="Копировать 📋",
                        fg_color=gui.COLOR_GREY,
                        hover_color=gui.COLOR_GREY_HOVER,
                    )
            except tk.TclError:
                pass  # карточку успели удалить

        timer["after_id"] = button.after(1500, restore)

    def _save_to_history(self, text: str) -> None:
        """Пишет результат в SQLite и сразу показывает карточку в истории."""
        if not text.strip():
            return
        if not self._history_available and not self.history.init_schema():
            return
        entry = self.history.add(text)
        if entry is None:
            return
        self._history_available = True
        self._prepend_history_card(entry)
        logger.info("в историю добавлена запись id=%d", entry.id)

    def _on_clear_history_click(self) -> None:
        """
        Первое нажатие просит подтверждение, второе — очищает историю.

        Модальных окон в приложении нет, поэтому подтверждение встроено
        в саму кнопку: она краснеет и меняет надпись на пять секунд.
        """
        if not self._history_available:
            return
        if not self._history_clear_armed:
            self._arm_history_clear()
            return
        self._cancel_history_clear()
        self._clear_history()

    def _arm_history_clear(self) -> None:
        """Взводит подтверждение очистки на _HISTORY_CLEAR_CONFIRM_MS."""
        self._cancel_history_clear()
        self._history_clear_armed = True
        self.btn_clear_history.configure(
            text="Точно очистить?",
            fg_color=gui.COLOR_RED,
            hover_color=gui.COLOR_RED_HOVER,
        )
        self._history_clear_timer = self.after(
            _HISTORY_CLEAR_CONFIRM_MS, self._cancel_history_clear
        )

    def _cancel_history_clear(self) -> None:
        """Снимает подтверждение и возвращает кнопке исходный вид."""
        if self._history_clear_timer is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._history_clear_timer)
            self._history_clear_timer = None
        if not self._history_clear_armed:
            return
        self._history_clear_armed = False
        with contextlib.suppress(tk.TclError):  # окно уже закрывается
            if self.btn_clear_history.winfo_exists():
                self.btn_clear_history.configure(
                    text="Очистить историю 🗑",
                    fg_color=gui.COLOR_GREY,
                    hover_color=gui.COLOR_GREY_HOVER,
                )

    def _clear_history(self) -> None:
        """Удаляет записи из базы и все карточки из вкладки."""
        removed = self.history.clear()
        for widget in self.history_scroll_frame.pack_slaves():
            with contextlib.suppress(tk.TclError):
                widget.destroy()
        self._history_first = None
        self._history_placeholder = None
        self._show_history_placeholder("История пуста")
        self.lbl_status.configure(
            text=f"История очищена (удалено записей: {removed})", text_color="gray"
        )
        logger.info("история очищена, удалено записей: %d", removed)

    # ------------------------------------------------------------------
    # модели
    # ------------------------------------------------------------------

    def _selected_model_name(self) -> str:
        """Чистое имя выбранной модели — то, что уходит в whisper.load_model()."""
        return labels.clean_model_label(self.whisper_var.get())

    def refresh_whisper_models(self) -> set[str]:
        """
        Перечитывает кэш и обновляет подписи и цвета в выпадающем списке.

        Возвращает новое множество доступных моделей (его же запрашивает
        диспетчер, чтобы не держать собственное состояние).
        """
        self.downloaded_whisper_models = dm.get_downloaded_whisper_models()

        selected = self._selected_model_name()
        if selected not in dm.WHISPER_MODELS:
            selected = dm.WHISPER_MODELS[0]

        self.whisper_display_options = labels.build_model_labels(
            dm.WHISPER_MODELS, self.downloaded_whisper_models
        )
        self.whisper_menu.configure(values=self.whisper_display_options)
        # configure(values=...) пересобирает пункты — цвета накладываем заново.
        gui.apply_menu_item_colors(
            self.whisper_menu, dm.WHISPER_MODELS, self.downloaded_whisper_models
        )
        self.whisper_var.set(selected)
        self.whisper_display_var.set(
            labels.label_for_model(self.whisper_display_options, selected)
        )
        return self.downloaded_whisper_models

    def _on_whisper_model_change(self, choice: str) -> None:
        """Сохраняет чистое имя модели и показывает каноническую подпись."""
        clean_name = labels.clean_model_label(choice)
        self.whisper_var.set(clean_name)
        self.whisper_display_var.set(
            labels.label_for_model(self.whisper_display_options, clean_name)
        )

        if clean_name in self.downloaded_whisper_models:
            self.lbl_status.configure(
                text=f"Выбрана модель '{clean_name}' (Локально)", text_color="green"
            )
        else:
            self.lbl_status.configure(
                text=f"Выбрана модель '{clean_name}' (Будет загружена при использовании)",
                text_color="orange",
            )

    def open_model_manager(self) -> None:
        """Открывает диспетчер моделей; повторный клик поднимает открытое окно."""
        if self.model_manager is not None and self.model_manager.winfo_exists():
            self.model_manager.lift()
            self.model_manager.focus_force()
            return
        self.model_manager = gui.ModelManagerWindow(
            self,
            downloaded_models=self.downloaded_whisper_models,
            on_models_changed=self.refresh_whisper_models,
            on_busy_changed=self.set_download_active,
            on_closed=self._on_model_manager_closed,
        )

    def _on_model_manager_closed(self) -> None:
        """Забывает ссылку на закрытый диспетчер моделей."""
        self.model_manager = None

    # ------------------------------------------------------------------
    # блокировка элементов управления
    # ------------------------------------------------------------------

    def set_download_active(self, active: bool) -> None:
        """Блокирует управление транскрибацией на время скачивания модели."""
        self._download_active = active
        self._apply_controls()

    def _set_transcription_active(self, active: bool) -> None:
        """Помечает, что идёт распознавание, и обновляет кнопки."""
        self._transcription_active = active
        self._apply_controls()

    def _apply_controls(self) -> None:
        """Согласует состояние кнопок с текущими фоновыми задачами."""
        busy = self._download_active or self._transcription_active
        self.btn_load_audio.configure(state="disabled" if busy else "normal")
        self.whisper_menu.configure(state="disabled" if busy else "normal")
        # Кнопку записи нельзя блокировать во время записи: ею же её и останавливают.
        if self.recorder.is_recording:
            self.btn_record_mic.configure(state="normal")
        else:
            self.btn_record_mic.configure(state="disabled" if busy else "normal")

    # ------------------------------------------------------------------
    # индикатор прогресса
    # ------------------------------------------------------------------

    def _start_progress_animation(self) -> None:
        """
        Включает бегущую полосу.

        Whisper не сообщает прогресс распознавания, поэтому честнее показать
        «идёт работа», чем неподвижную полосу на нуле.
        """
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start()

    def _stop_progress_animation(self, value: float | None = None) -> None:
        """Возвращает полосу в обычный режим и при необходимости задаёт значение."""
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        if value is not None:
            self.progress_bar.set(value)

    # ------------------------------------------------------------------
    # запись с микрофона
    # ------------------------------------------------------------------

    def toggle_recording(self) -> None:
        """Переключает запись: первый клик начинает, второй останавливает."""
        if self.recorder.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        """Начинает запись. Модель загрузится позже, уже в рабочем потоке."""
        if self.recorder.is_recording:
            return
        try:
            self.recorder.start()
        except ww.RecorderError as exc:
            logger.error("не удалось начать запись: %s", exc)
            self.lbl_status.configure(text=f"Ошибка записи: {exc}", text_color=gui.COLOR_RED)
            return

        self.btn_record_mic.configure(
            text="⏹ Остановить запись",
            fg_color=gui.COLOR_RED_HOVER,
            hover_color=gui.COLOR_RED_ACTIVE,
        )
        self.lbl_status.configure(
            text="🔴 Запись идёт... Нажмите «Остановить запись», когда закончите",
            text_color=gui.COLOR_RED,
        )
        self.txt_transcript.delete("1.0", "end")
        self._apply_controls()

    def stop_recording(self) -> None:
        """Останавливает запись и отправляет WAV на распознавание."""
        self.btn_record_mic.configure(
            text="🎤 Записать с микрофона",
            fg_color=gui.COLOR_RED,
            hover_color=gui.COLOR_RED_HOVER,
        )
        try:
            temp_path = self.recorder.stop()
        except ww.RecorderError as exc:
            logger.error("не удалось сохранить запись: %s", exc)
            self.lbl_status.configure(
                text=f"Ошибка сохранения аудио: {exc}", text_color=gui.COLOR_RED
            )
            self._apply_controls()
            return

        self._apply_controls()
        if temp_path is None:
            self.lbl_status.configure(text="Аудио не записано", text_color="gray")
            return
        logger.info(
            "запись завершена: %.1f с, %d кадров в файле",
            self.recorder.duration,
            self.recorder.frames_written,
        )
        self._submit_transcription(temp_path, is_temp=True)

    def _on_recording_tick(self, seconds: float) -> None:
        """Вызывается потоком-писателем рекордера раз в секунду."""
        self.update_status(f"Запись идёт... {int(seconds)} сек")

    # ------------------------------------------------------------------
    # транскрибация
    # ------------------------------------------------------------------

    def start_transcription_thread(self) -> None:
        """Спрашивает файл и ставит его в очередь на распознавание."""
        file_path = filedialog.askopenfilename(
            title="Выберите аудиофайл",
            filetypes=[
                ("Аудио файлы", "*.mp3 *.wav *.ogg *.flac *.m4a"),
                ("Все файлы", "*.*"),
            ],
        )
        if not file_path:
            self.lbl_status.configure(text="Файл не выбран", text_color="gray")
            return

        self.txt_transcript.delete("1.0", "end")
        self._submit_transcription(Path(file_path), is_temp=False)

    def _submit_transcription(self, audio_path: Path, is_temp: bool) -> None:
        """
        Передаёт файл рабочему потоку.

        Модель загрузится внутри задачи, поэтому интерфейс не замирает
        даже при первой загрузке весов `large`.
        """
        self._set_transcription_active(True)
        self._pending_temp = audio_path if is_temp else None
        self.lbl_status.configure(
            text=f"Обработка: {audio_path.name}", text_color=gui.COLOR_ACCENT
        )
        self._start_progress_animation()
        logger.info("задача распознавания: %s (временный=%s)", audio_path, is_temp)
        self.worker.submit(
            audio_path,
            on_done=lambda text, path: self.after(
                0, self._on_transcription_done, text, path, is_temp
            ),
            on_error=lambda exc: self.after(
                0, self._on_transcription_failed, exc, audio_path, is_temp
            ),
            on_status=lambda message: self.after(0, self.update_status, message),
        )

    def _on_transcription_done(self, text: str, path: Path, is_temp: bool) -> None:
        """Успешное распознавание: показываем текст и убираем временный файл."""
        self.txt_transcript.insert("1.0", text or "(речь не распознана)")
        self._stop_progress_animation(1.0)
        self.lbl_status.configure(text="Готово", text_color="green")
        if is_temp:
            ww.cleanup_temp_file(path)
            self._pending_temp = None
        self._set_transcription_active(False)
        logger.info("распознавание завершено, символов: %d", len(text))
        self._save_to_history(text)

    def _on_transcription_failed(self, exc: BaseException, path: Path, is_temp: bool) -> None:
        """Ошибка распознавания: сообщаем и всё равно чистим временный файл."""
        if is_temp:
            ww.cleanup_temp_file(path)
            self._pending_temp = None
        self._stop_progress_animation(0)
        self.lbl_status.configure(text=f"Ошибка транскрибации: {exc}", text_color=gui.COLOR_RED)
        self._set_transcription_active(False)
        logger.error("ошибка распознавания: %s", exc)

    # ------------------------------------------------------------------
    # обновление интерфейса (безопасно из рабочих потоков)
    # ------------------------------------------------------------------

    def update_progressbar(self, value: float) -> None:
        """Устанавливает значение прогресс-бара из рабочего потока."""
        self.after(0, lambda: self.progress_bar.set(value))

    def update_status(self, message: str) -> None:
        """Показывает статус из рабочего потока."""
        self.after(0, lambda: self.lbl_status.configure(text=message))


def main() -> None:
    """Настраивает журнал, читает настройки и запускает цикл обработки событий."""
    log_path = app_settings.setup_logging()
    logger.info("NordScribe запускается (журнал: %s)", log_path)

    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")

    config = app_settings.Settings.load()
    logger.info(
        "настройки: модель=%s, язык=%s, тишина=%s",
        config.model,
        config.language,
        config.trim_silence,
    )

    app = NordScribeApp(config)
    app.mainloop()


if __name__ == "__main__":
    main()
