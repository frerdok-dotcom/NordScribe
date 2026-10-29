"""Main window: wires the UI to services and owns application state.

The window is the composition root for the Tk side. It creates the recorder,
transcriber and model manager, routes worker callbacks through
:class:`~nordscribe.ui.dispatcher.UiDispatcher`, and saves settings on exit.
Business rules stay in services and the SQLite repository.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from tkinter import filedialog as fd

import customtkinter as ctk

from nordscribe.core.errors import RecorderError
from nordscribe.core.models import (
    DEFAULT_MODEL_NAME,
    MODEL_NAMES,
    build_model_labels,
    clean_model_label,
    label_for_model,
)
from nordscribe.infra.history import TranscriptionHistory
from nordscribe.infra.settings import Settings
from nordscribe.services.model_service import ModelService
from nordscribe.services.preflight import has_fatal, run_checks, summarize
from nordscribe.services.recorder import (
    DEFAULT_SAMPLE_RATE,
    AudioRecorder,
    cleanup_temp_file,
)
from nordscribe.services.transcriber import Transcriber
from nordscribe.ui.dispatcher import UiDispatcher
from nordscribe.ui.history_tab import HistoryTab
from nordscribe.ui.model_manager import ModelManagerWindow
from nordscribe.ui.model_menu import apply_menu_item_colors
from nordscribe.ui.theme import (
    COLOR_ACCENT,
    COLOR_GREY,
    COLOR_GREY_HOVER,
    COLOR_RED,
    STATUS_GRAY,
    STATUS_GREEN,
    STATUS_ORANGE,
    pick_emoji_font_family,
    ui_font,
)
from nordscribe.ui.transcription_tab import IDLE_STATUS, TranscriptionTab

__all__ = ["MainWindow"]

logger = logging.getLogger(__name__)

#: Accepted window geometry: ``1100x700`` or ``1100x700+100+50``.
_GEOMETRY_RE = re.compile(r"^\d+x\d+([+-]\d+[+-]\d+)?$")


class MainWindow(ctk.CTk):
    """Main application window."""

    SAMPLE_RATE = DEFAULT_SAMPLE_RATE

    def __init__(
        self,
        settings: Settings,
        *,
        model_service: ModelService,
        history: TranscriptionHistory,
    ) -> None:
        super().__init__()

        self.settings = settings
        self._model_service = model_service
        self._history = history
        self._shutting_down = False

        self._dispatcher = UiDispatcher(self)
        self._dispatcher.start()

        self.title("NordScribe - Локальная Транскрибация")
        self._apply_saved_geometry()

        # Two distinct variables: the clean name goes to Whisper, the display
        # value is what the option menu renders (marker + name).
        self._downloaded: set[str] = model_service.downloaded()
        selected = settings.model if settings.model in MODEL_NAMES else DEFAULT_MODEL_NAME
        labels = build_model_labels(MODEL_NAMES, self._downloaded)
        self._whisper_var = ctk.StringVar(value=selected)
        self._display_var = ctk.StringVar(value=label_for_model(labels, selected))

        self._download_active = False
        self._transcription_active = False
        self._pending_temp: Path | None = None
        self._model_manager: ModelManagerWindow | None = None

        self._emoji_family = pick_emoji_font_family()
        self._history_available = history.init_schema()

        self._recorder = AudioRecorder(
            sample_rate=self.SAMPLE_RATE,
            on_tick=self._on_recording_tick,
            trim_silence=settings.trim_silence,
        )
        self._transcriber = Transcriber(
            model_name_provider=self._selected_model_name,
            language=settings.language,
        )

        self._build_layout()
        apply_menu_item_colors(
            self.whisper_menu,
            MODEL_NAMES,
            self._downloaded,
        )
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._run_preflight()
        self._load_history()

    # ------------------------------------------------------------------
    # startup and shutdown
    # ------------------------------------------------------------------

    def _apply_saved_geometry(self) -> None:
        geometry = (self.settings.window_geometry or "").strip()
        if _GEOMETRY_RE.match(geometry):
            self.geometry(geometry)
        else:
            logger.debug("geometry %r is invalid, using the default", geometry)
            self.geometry("1100x700")

    def _run_preflight(self) -> None:
        """Check the environment and report problems in the status bar."""
        try:
            results = run_checks(self._model_service.cache_dir)
        except Exception:
            logger.exception("environment checks failed")
            return
        summary = summarize(results)
        if summary:
            color = COLOR_RED if has_fatal(results) else STATUS_ORANGE
            self.transcription.set_status(summary, color)
        logger.info(
            "environment: %s",
            "; ".join(f"{item.name}={'ok' if item.ok else 'no'}" for item in results),
        )

    def destroy(self) -> None:
        """Stop workers, persist settings and close the window."""
        if self._shutting_down:
            return
        self._shutting_down = True
        try:
            self._shutdown()
        except Exception:
            logger.exception("error while shutting down")
        finally:
            super().destroy()

    def _shutdown(self) -> None:
        self.history_tab.cancel_clear()
        self.history_tab.disable_mousewheel()

        if self._recorder.is_recording:
            try:
                cleanup_temp_file(self._recorder.stop())
            except RecorderError as exc:
                logger.warning("could not stop recording cleanly: %s", exc)
        if cleanup_temp_file(self._pending_temp):
            logger.info("removed an unfinished temporary recording")
        self._pending_temp = None

        self.settings.update(
            model=self._selected_model_name(),
            window_geometry=self.geometry(),
        )
        self.settings.save()

        self._transcriber.shutdown(wait=False)
        self._dispatcher.stop()
        logger.info("application stopped")

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self._build_settings_row()
        self._build_tabbed_area()

    def _build_settings_row(self) -> None:
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

        self.whisper_menu = ctk.CTkOptionMenu(
            self.settings_frame,
            variable=self._display_var,
            values=build_model_labels(MODEL_NAMES, self._downloaded),
            command=self._on_whisper_model_change,
            width=140,
            height=30,
            font=self._ui_font(size=13),
            dropdown_font=self._ui_font(size=13),
        )
        self.whisper_menu.grid(row=0, column=1, sticky="w")

        self.btn_manage_models = ctk.CTkButton(
            self.settings_frame,
            text="⚙ Управление моделями",
            command=self.open_model_manager,
            width=190,
            height=30,
            fg_color=COLOR_GREY,
            hover_color=COLOR_GREY_HOVER,
        )
        self.btn_manage_models.grid(row=0, column=2, sticky="e")

    def _ui_font(self, size: int = 13, bold: bool = False) -> ctk.CTkFont:
        return ui_font(self._emoji_family, size=size, bold=bold)

    def _build_tabbed_area(self) -> None:
        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=15, pady=(0, 15))
        self.transcription = TranscriptionTab(
            self.tabview.add("Транскрибация"),
            emoji_family=self._emoji_family,
            on_load_audio=self.start_transcription_thread,
            on_toggle_recording=self.toggle_recording,
        )
        self.history_tab = HistoryTab(
            self.tabview.add("История"),
            emoji_family=self._emoji_family,
            on_cleared=self._clear_history,
        )
        if not self._history_available:
            self.history_tab.set_enabled(False)

    # ------------------------------------------------------------------
    # history
    # ------------------------------------------------------------------

    def _load_history(self) -> None:
        if not self._history_available:
            if self.transcription.lbl_status.cget("text") == IDLE_STATUS:
                self.transcription.set_status(
                    "История недоступна: не удалось открыть базу данных",
                    STATUS_ORANGE,
                )
            self.history_tab.show_placeholder("История недоступна")
            return

        entries = self._history.all()
        self.history_tab.load(entries)
        logger.info("history: loaded %d entries", len(entries))

    def _save_to_history(self, text: str) -> None:
        if not text.strip():
            return
        if not self._history_available and not self._history.init_schema():
            return
        entry = self._history.add(text)
        if entry is None:
            return
        self._history_available = True
        self.history_tab.set_enabled(True)
        self.history_tab.prepend(entry)
        logger.info("history: added entry id=%d", entry.id)

    def _clear_history(self) -> None:
        removed = self._history.clear()
        self.history_tab.clear_cards()
        self.history_tab.show_placeholder("История пуста")
        self.transcription.set_status(
            f"История очищена (удалено записей: {removed})",
            STATUS_GRAY,
        )
        logger.info("history cleared, removed %d entries", removed)

    # ------------------------------------------------------------------
    # models
    # ------------------------------------------------------------------

    def _selected_model_name(self) -> str:
        """Clean model name passed to ``whisper.load_model()``."""
        return clean_model_label(self._whisper_var.get()) or DEFAULT_MODEL_NAME

    def refresh_models(self) -> None:
        """Re-read the cache and update labels and colours in the menu."""
        self._downloaded = self._model_service.downloaded()
        selected = self._selected_model_name()
        if selected not in MODEL_NAMES:
            selected = DEFAULT_MODEL_NAME

        labels = build_model_labels(MODEL_NAMES, self._downloaded)
        self.whisper_menu.configure(values=labels)
        apply_menu_item_colors(self.whisper_menu, MODEL_NAMES, self._downloaded)
        self._whisper_var.set(selected)
        self._display_var.set(label_for_model(labels, selected))

    def _on_whisper_model_change(self, choice: str) -> None:
        clean_name = clean_model_label(choice)
        self._whisper_var.set(clean_name)
        self._display_var.set(
            label_for_model(build_model_labels(MODEL_NAMES, self._downloaded), clean_name)
        )

        if clean_name in self._downloaded:
            self.transcription.set_status(
                f"Выбрана модель '{clean_name}' (Локально)",
                STATUS_GREEN,
            )
        else:
            self.transcription.set_status(
                f"Выбрана модель '{clean_name}' (Будет загружена при использовании)",
                STATUS_ORANGE,
            )

    def open_model_manager(self) -> None:
        """Open the model manager, or raise the existing window."""
        if self._model_manager is not None and self._model_manager.winfo_exists():
            self._model_manager.lift()
            self._model_manager.focus_force()
            return
        self._model_manager = ModelManagerWindow(
            self,
            service=self._model_service,
            post=self._dispatcher.post,
            on_models_changed=self.refresh_models,
            on_busy_changed=self.set_download_active,
            on_closed=self._on_model_manager_closed,
        )

    def _on_model_manager_closed(self) -> None:
        self._model_manager = None

    # ------------------------------------------------------------------
    # busy state
    # ------------------------------------------------------------------

    def set_download_active(self, active: bool) -> None:
        """Disable transcription while weights are being downloaded."""
        self._download_active = active
        self._apply_controls()

    def _set_transcription_active(self, active: bool) -> None:
        self._transcription_active = active
        self._apply_controls()

    def _apply_controls(self) -> None:
        busy = self._download_active or self._transcription_active
        state = "disabled" if busy else "normal"
        self.transcription.btn_load_audio.configure(state=state)
        self.whisper_menu.configure(state=state)
        if self._recorder.is_recording:
            self.transcription.btn_record_mic.configure(state="normal")
        else:
            self.transcription.btn_record_mic.configure(state=state)

    # ------------------------------------------------------------------
    # recording
    # ------------------------------------------------------------------

    def toggle_recording(self) -> None:
        if self._recorder.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        if self._recorder.is_recording:
            return
        try:
            self._recorder.start()
        except RecorderError as exc:
            logger.error("cannot start recording: %s", exc)
            self.transcription.set_status(f"Ошибка записи: {exc}", COLOR_RED)
            return

        self.transcription.set_recording(True)
        self.transcription.set_status(
            "🔴 Запись идёт... Нажмите «Остановить запись», когда закончите",
            COLOR_RED,
        )
        self.transcription.clear_transcript()
        self._apply_controls()

    def stop_recording(self) -> None:
        self.transcription.set_recording(False)
        try:
            temp_path = self._recorder.stop()
        except RecorderError as exc:
            logger.error("cannot save recording: %s", exc)
            self.transcription.set_status(f"Ошибка сохранения аудио: {exc}", COLOR_RED)
            self._apply_controls()
            return

        self._apply_controls()
        if temp_path is None:
            self.transcription.set_status("Аудио не записано", STATUS_GRAY)
            return
        logger.info(
            "recording finished: %.1f s, %d frames in file",
            self._recorder.duration,
            self._recorder.frames_written,
        )
        self._submit_transcription(temp_path, is_temp=True)

    def _on_recording_tick(self, seconds: float) -> None:
        """Writer-thread callback: queue a UI update."""
        self._dispatcher.post(self._show_recording_tick, seconds)

    def _show_recording_tick(self, seconds: float) -> None:
        self.transcription.set_status(f"Запись идёт... {int(seconds)} сек")

    # ------------------------------------------------------------------
    # transcription
    # ------------------------------------------------------------------

    def start_transcription_thread(self) -> None:
        file_path = fd.askopenfilename(
            title="Выберите аудиофайл",
            filetypes=[
                ("Аудио файлы", "*.mp3 *.wav *.ogg *.flac *.m4a"),
                ("Все файлы", "*.*"),
            ],
        )
        if not file_path:
            self.transcription.set_status("Файл не выбран", STATUS_GRAY)
            return

        self.transcription.clear_transcript()
        self._submit_transcription(Path(file_path), is_temp=False)

    def _submit_transcription(self, audio_path: Path, is_temp: bool) -> None:
        self._set_transcription_active(True)
        self._pending_temp = audio_path if is_temp else None
        self.transcription.set_status(f"Обработка: {audio_path.name}", COLOR_ACCENT)
        self.transcription.start_progress_animation()
        logger.info("transcription job: %s (temporary=%s)", audio_path, is_temp)

        self._transcriber.submit(
            audio_path,
            on_done=lambda text, path: self._dispatcher.post(
                self._on_transcription_done, text, path, is_temp
            ),
            on_error=lambda exc: self._dispatcher.post(
                self._on_transcription_failed, exc, audio_path, is_temp
            ),
            on_status=lambda message: self._dispatcher.post(self._set_status_from_worker, message),
        )

    def _set_status_from_worker(self, message: str) -> None:
        self.transcription.set_status(message)

    def _on_transcription_done(self, text: str, path: Path, is_temp: bool) -> None:
        self.transcription.show_transcript(text or "(речь не распознана)")
        self.transcription.stop_progress_animation(1.0)
        self.transcription.set_status("Готово", STATUS_GREEN)
        if is_temp:
            cleanup_temp_file(path)
            self._pending_temp = None
        self._set_transcription_active(False)
        logger.info("transcription finished, characters: %d", len(text))
        self._save_to_history(text)

    def _on_transcription_failed(
        self,
        exc: BaseException,
        path: Path,
        is_temp: bool,
    ) -> None:
        if is_temp:
            cleanup_temp_file(path)
            self._pending_temp = None
        self.transcription.stop_progress_animation(0)
        self.transcription.set_status(f"Ошибка транскрибации: {exc}", COLOR_RED)
        self._set_transcription_active(False)
        logger.error("transcription failed: %s", exc)
