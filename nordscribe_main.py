import customtkinter as ctk
import threading
import time
import os
import hashlib
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import tkinter as tk
from pathlib import Path
from typing import Callable, Optional, Set, List
import whisper
from tkinter import filedialog
import numpy as np
import sounddevice as sd
from scipy.io.wavfile import write

# Настройки внешнего вида
ctk.set_appearance_mode("Dark")  # Темная тема
ctk.set_default_color_theme("blue")

# Папка, в которую Whisper складывает веса моделей по умолчанию.
# Именно отсюда их читает whisper.load_model(), поэтому качать нужно строго сюда.
WHISPER_CACHE_DIR = Path.home() / ".cache" / "whisper"

# Пять официальных моделей, доступных в интерфейсе приложения.
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large"]

# Примерный вес файлов на диске (для моделей, которых ещё нет в кэше).
WHISPER_MODEL_SIZES = {
    "tiny": "~75 МБ",
    "base": "~140 МБ",
    "small": "~460 МБ",
    "medium": "~1.5 ГБ",
    "large": "~2.9 ГБ",
}

# Цветные маркеры статуса для выпадающего списка главного окна.
# Раньше для «не скачана» стоял белый круг ⚪ — на тёмной теме он выглядел
# как монохромный радио-индикатор и сливался с фоном. Теперь оба состояния
# различаются и формой подписи, и цветом.
MARKER_DOWNLOADED = "🟢"   # веса уже лежат в ~/.cache/whisper
MARKER_MISSING = "🔵"      # требуется загрузка (альтернатива — "🟡")

# Любые маркеры, которые нужно снять перед передачей имени в Whisper.
# ⚪ и 🟡 оставлены для совместимости со старыми подписями.
_DISPLAY_MARKERS = ("🟢", "🔵", "🟡", "⚪")

# Палитра для кнопок диспетчера. Приложение всегда работает в тёмной теме
# (см. set_appearance_mode выше), поэтому цвета заданы явно.
COLOR_BLUE = "#1F6AA5"
COLOR_BLUE_HOVER = "#144870"
COLOR_GREY = "#3A3A3A"
COLOR_GREY_HOVER = "#4A4A4A"
COLOR_RED = "#D32F2F"
COLOR_RED_HOVER = "#B71C1C"
COLOR_ACCENT = "#3B8ED0"

# Цвета текста пунктов выпадающего списка. Дублируют смысл маркеров:
# цветные эмодзи рисуются не во всех сборках Tk и зависят от шрифта,
# а цвет текста виден всегда.
MENU_COLOR_DOWNLOADED = "#4CAF50"
MENU_COLOR_MISSING = "#3B8ED0"

# Резервные адреса на случай, если приватный словарь whisper._MODELS
# исчезнет или переименуется в новой версии библиотеки.
# ВАЖНО: последний сегмент пути — это имя файла на диске (для "large" это
# large-v3.pt!), а предпоследний — ожидаемая контрольная сумма SHA-256.
_FALLBACK_MODEL_URLS = {
    "tiny": "https://openaipublic.azureedge.net/main/whisper/models/65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9/tiny.pt",
    "base": "https://openaipublic.azureedge.net/main/whisper/models/ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e/base.pt",
    "small": "https://openaipublic.azureedge.net/main/whisper/models/9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794/small.pt",
    "medium": "https://openaipublic.azureedge.net/main/whisper/models/345ae4da62f9b3d59415adc60127b97c714f32e89e936602e85993674d08dcb1/medium.pt",
    "large": "https://openaipublic.azureedge.net/main/whisper/models/e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb/large-v3.pt",
}


def pick_emoji_font_family() -> Optional[str]:
    """
    Подбирает семейство шрифта, которое умеет рисовать ЦВЕТНЫЕ эмодзи.

    Это критично для маркеров статуса: если оставить шрифт по умолчанию,
    Tk берёт монохромный глиф из fallback-шрифта, и 🟢 с 🔵 превращаются
    в одинаковые серые кружки — статусы становятся неразличимы.
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


class DownloadCancelled(Exception):
    """Загрузка прервана пользователем (закрытие окна диспетчера)."""


class ChecksumError(Exception):
    """Скачанный файл повреждён: SHA-256 не совпала с ожидаемой."""


def get_model_url(model_name: str) -> Optional[str]:
    """
    Возвращает прямой URL файла модели на серверах OpenAI.

    Источник истины — словарь whisper._MODELS: он гарантированно совпадает
    с тем, что библиотека ожидает найти в кэше. Если приватный словарь
    недоступен, используется резервная таблица.
    """
    urls = getattr(whisper, "_MODELS", None)
    if isinstance(urls, dict) and urls.get(model_name):
        return urls[model_name]
    return _FALLBACK_MODEL_URLS.get(model_name)


def model_file_path(model_name: str, cache_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Возвращает путь к файлу весов модели в кэше.

    Имя файла берётся из URL, а не из имени модели: для "large" это
    large-v3.pt — если сохранить его как large.pt, whisper.load_model()
    файл не найдёт и начнёт скачивание заново.
    """
    url = get_model_url(model_name)
    if not url:
        return None
    filename = os.path.basename(urllib.parse.urlsplit(url).path)
    return Path(cache_dir or WHISPER_CACHE_DIR) / filename


def format_bytes(num_bytes: float) -> str:
    """Переводит число байт в человекочитаемый вид: 461234567 -> '440 МБ'."""
    size = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024 or unit == "ТБ":
            if unit in ("Б", "КБ", "МБ"):
                return f"{size:.0f} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ТБ"


def sha256_of_file(path: Path, chunk_size: int = 1024 * 1024,
                   is_cancelled: Optional[Callable[[], bool]] = None) -> str:
    """Считает SHA-256 файла блоками, не загружая его целиком в память."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            if is_cancelled and is_cancelled():
                raise DownloadCancelled()
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def download_whisper_model(
    url: str,
    dest_dir: Path,
    on_progress: Optional[Callable[[int, int], None]] = None,
    on_status: Optional[Callable[[str], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    chunk_size: int = 256 * 1024,
) -> Path:
    """
    Честно скачивает файл модели в dest_dir, сообщая реальный прогресс.

    Имя файла и контрольная сумма берутся из самого URL вида
    https://.../<sha256>/<filename>.pt — ровно так же, как это делает
    whisper._download(), поэтому скачанный файл библиотека примет как свой.

    Данные пишутся во временный "<filename>.part" и переименовываются в
    целевой файл только после успешной проверки SHA-256. При любой ошибке
    (обрыв сети, HTTP-ошибка, повреждение, отмена) временный файл удаляется,
    а исключение пробрасывается наверх.

    Чтение идёт блоками через urlopen, а не urlretrieve: нужно уметь
    остановиться по требованию пользователя и точно знать Content-Length.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    segments = urllib.parse.urlsplit(url).path.strip("/").split("/")
    filename = segments[-1]
    expected_sha256 = segments[-2] if len(segments) >= 2 else None
    if expected_sha256 and len(expected_sha256) != 64:
        expected_sha256 = None  # не похоже на hex-дайджест — проверять нечем

    dest = dest_dir / filename
    part = dest_dir / (filename + ".part")

    def emit(callback, *args):
        if callback is not None:
            callback(*args)

    def check_cancel():
        if is_cancelled and is_cancelled():
            raise DownloadCancelled()

    part.unlink(missing_ok=True)
    emit(on_status, "Подключение к серверу...")

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "NordScribe"})
        with urllib.request.urlopen(request, timeout=30) as response, open(part, "wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            emit(on_progress, 0, total)

            while True:
                check_cancel()
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                handle.write(chunk)
                received += len(chunk)
                emit(on_progress, received, total)

        # Сервер закрыл соединение раньше времени — файл заведомо неполный.
        if total and received < total:
            raise IOError(
                f"соединение прервано: получено {format_bytes(received)} "
                f"из {format_bytes(total)}"
            )

        if expected_sha256:
            emit(on_status, "Проверка целостности файла...")
            actual = sha256_of_file(part, is_cancelled=is_cancelled)
            if actual != expected_sha256:
                raise ChecksumError(
                    "контрольная сумма не совпала — файл повреждён при загрузке"
                )
    except BaseException:
        part.unlink(missing_ok=True)
        raise

    os.replace(part, dest)
    return dest


def describe_download_error(exc: BaseException) -> str:
    """Превращает исключение в понятное пользователю сообщение."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"сервер вернул ошибку {exc.code} ({exc.reason})"
    if isinstance(exc, urllib.error.URLError):
        return f"нет соединения с сервером ({exc.reason})"
    if isinstance(exc, ChecksumError):
        return str(exc)
    if isinstance(exc, TimeoutError):
        return "превышено время ожидания ответа сервера"
    text = str(exc).strip()
    return text if text else exc.__class__.__name__


def get_downloaded_whisper_models() -> Set[str]:
    """
    Сканирует кэш ~/.cache/whisper/ и возвращает множество имён скачанных моделей.

    Файл large-v3.pt учитывается и как 'large-v3', и как 'large' — именно
    под именем 'large' его запрашивает whisper.load_model().
    """
    cache_dir = WHISPER_CACHE_DIR
    official_models = [
        'tiny', 'tiny.en',
        'base', 'base.en',
        'small', 'small.en',
        'medium', 'medium.en',
        'large', 'large-v1', 'large-v2', 'large-v3', 'turbo'
    ]
    downloaded = set()

    if not cache_dir.exists():
        return downloaded

    try:
        files = list(cache_dir.glob("*.pt"))
    except PermissionError:
        return downloaded

    for file_path in files:
        filename = file_path.name
        model_key = filename[:-3]  # Убираем расширение .pt

        for official_name in official_models:
            if model_key == official_name:
                downloaded.add(official_name)
                # Для large-* версий также добавляем базовое имя 'large'
                if official_name.startswith('large'):
                    downloaded.add('large')
                break

    return downloaded


class ModelManagerWindow(ctk.CTkToplevel):
    """Всплывающее окно «Диспетчер моделей»: скачивание и удаление весов Whisper."""

    def __init__(self, app: "NordScribeApp"):
        super().__init__(app)
        self.app = app
        self._alive = True
        self._downloading: Optional[str] = None
        self._cancel_requested = False
        self._pending_delete: Optional[str] = None
        self._pending_delete_timer = None
        self._rows = {}

        self.title("Диспетчер моделей Whisper")
        self.resizable(False, False)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="Модели Whisper",
            font=ctk.CTkFont(size=16, weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(14, 2))

        ctk.CTkLabel(
            self,
            text=f"Файлы сохраняются в {WHISPER_CACHE_DIR}",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        ).grid(row=1, column=0, sticky="w", padx=15, pady=(0, 10))

        # --- Список моделей ---
        self.list_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.list_frame.grid(row=2, column=0, sticky="ew", padx=15)
        self.list_frame.grid_columnconfigure(0, weight=1)

        for index, name in enumerate(WHISPER_MODELS):
            self._build_row(index, name)

        # --- Общий индикатор прогресса ---
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
            wraplength=550
        )
        self.lbl_progress.grid(row=4, column=0, sticky="ew", padx=15, pady=(0, 10))

        ctk.CTkButton(
            self,
            text="Закрыть",
            width=120,
            height=32,
            fg_color=COLOR_GREY,
            hover_color=COLOR_GREY_HOVER,
            command=self._on_close
        ).grid(row=5, column=0, sticky="e", padx=15, pady=(0, 14))

        self._refresh_all()

        # Окно должно оказаться поверх главного, а не за ним.
        self.update_idletasks()
        self._center_over_parent()
        self.after(10, self._bring_to_front)

    # --- Построение строк списка ---

    def _build_row(self, index: int, name: str):
        row = ctk.CTkFrame(self.list_frame, corner_radius=6)
        row.grid(row=index, column=0, sticky="ew", pady=3)
        row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            row,
            text=name,
            width=70,
            anchor="w",
            font=ctk.CTkFont(size=14, weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=(12, 6), pady=8)

        size_label = ctk.CTkLabel(
            row,
            text="",
            width=90,
            anchor="w",
            text_color="gray",
            font=ctk.CTkFont(size=12)
        )
        size_label.grid(row=0, column=1, sticky="w", padx=(0, 10))

        status = ctk.CTkLabel(row, text="", width=130, anchor="e", font=ctk.CTkFont(size=12))
        status.grid(row=0, column=2, sticky="e", padx=(0, 10))

        button = ctk.CTkButton(
            row,
            text="Скачать",
            width=140,
            height=30,
            command=lambda n=name: self._on_row_action(n)
        )
        button.grid(row=0, column=3, sticky="e", padx=(0, 12), pady=6)

        self._rows[name] = {"size": size_label, "status": status, "button": button}

    def _refresh_all(self):
        """Перерисовывает состояние всех строк: размер, статус и действие."""
        for name in WHISPER_MODELS:
            row = self._rows[name]
            downloaded = name in self.app.downloaded_whisper_models
            path = model_file_path(name)

            # Размер: фактический, если файл уже лежит на диске.
            if path is not None and path.exists():
                try:
                    row["size"].configure(text=format_bytes(path.stat().st_size))
                except OSError:
                    row["size"].configure(text=WHISPER_MODEL_SIZES[name])
            else:
                row["size"].configure(text=WHISPER_MODEL_SIZES[name])

            if self._downloading == name:
                row["status"].configure(text="Загрузка...", text_color=COLOR_ACCENT)
                row["button"].configure(
                    text="Скачивание...", state="disabled",
                    fg_color=COLOR_GREY, hover_color=COLOR_GREY
                )
            elif downloaded:
                row["status"].configure(text="Готова к работе", text_color="green")
                if self._pending_delete == name:
                    row["button"].configure(
                        text="Точно удалить?", state="normal",
                        fg_color=COLOR_RED, hover_color=COLOR_RED_HOVER
                    )
                else:
                    row["button"].configure(
                        text="Удалить", state="normal",
                        fg_color=COLOR_GREY, hover_color=COLOR_GREY_HOVER
                    )
            else:
                row["status"].configure(text="Не скачана", text_color="gray")
                row["button"].configure(
                    text="Скачать",
                    state="disabled" if self._downloading else "normal",
                    fg_color=COLOR_BLUE, hover_color=COLOR_BLUE_HOVER
                )

    # --- Вспомогательные методы окна ---

    def _center_over_parent(self):
        width, height = 600, 490
        self.geometry(f"{width}x{height}")
        parent = self.app
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _bring_to_front(self):
        if not self._alive:
            return
        self.lift()
        self.focus_force()

    def _ui(self, callback: Callable[[], None]):
        """Планирует обновление UI в главном потоке Tkinter."""
        if not self._alive:
            return
        try:
            self.after(0, callback)
        except tk.TclError:
            # Окно уже уничтожено — обновлять нечего.
            pass

    def _set_message(self, text: str, color: str = "gray"):
        self.lbl_progress.configure(text=text, text_color=color)

    # --- Действия в строке: скачать / удалить ---

    def _on_row_action(self, name: str):
        if self._downloading is not None:
            return  # во время загрузки строки заблокированы
        if name in self.app.downloaded_whisper_models:
            if self._pending_delete == name:
                self._cancel_pending_delete()
                self._delete_model(name)
            else:
                self._arm_pending_delete(name)
        else:
            self._start_download(name)

    def _arm_pending_delete(self, name: str):
        """Первое нажатие «Удалить» — запрашиваем подтверждение вторым нажатием."""
        self._cancel_pending_delete()
        self._pending_delete = name
        self._refresh_all()
        self._set_message(
            f"Нажмите «Точно удалить?» ещё раз, чтобы удалить веса '{name}'",
            COLOR_RED
        )
        self._pending_delete_timer = self.after(5000, self._cancel_pending_delete)

    def _cancel_pending_delete(self):
        if self._pending_delete_timer is not None:
            try:
                self.after_cancel(self._pending_delete_timer)
            except tk.TclError:
                pass
            self._pending_delete_timer = None
        if self._pending_delete is not None:
            self._pending_delete = None
            if self._alive:
                self._refresh_all()

    def _delete_model(self, name: str):
        path = model_file_path(name)
        if path is None:
            self._set_message(f"❌ Не удалось определить файл модели '{name}'", COLOR_RED)
            return

        removed = []
        freed = 0
        try:
            for target in (path, path.with_name(path.name + ".part")):
                if target.exists():
                    freed += target.stat().st_size
                    target.unlink()
                    removed.append(target.name)
        except OSError as exc:
            self.app.refresh_whisper_models()
            self._refresh_all()
            self._set_message(
                f"❌ Не удалось удалить {path.name}: {exc}. "
                f"Возможно, файл занят другой программой.",
                COLOR_RED
            )
            return

        self.app.refresh_whisper_models()
        self._refresh_all()
        self.progress_bar.set(0)

        if not removed:
            self._set_message(f"Файлы модели '{name}' в кэше не найдены")
        elif name in self.app.downloaded_whisper_models:
            # Например, удалили large-v3.pt, но остался large-v2.pt.
            self._set_message(
                f"Удалён {', '.join(removed)} ({format_bytes(freed)}), но модель "
                f"'{name}' всё ещё доступна — в кэше остались другие её веса.",
                "orange"
            )
        else:
            self._set_message(
                f"✓ Модель '{name}' удалена ({', '.join(removed)}), "
                f"освобождено {format_bytes(freed)}",
                "green"
            )

    # --- Загрузка ---

    def _start_download(self, name: str):
        if self._downloading is not None:
            return
        self._cancel_pending_delete()

        url = get_model_url(name)
        if not url:
            self._set_message(f"❌ Для модели '{name}' не найден адрес загрузки", COLOR_RED)
            return

        self._cancel_requested = False
        self._downloading = name
        self.progress_bar.set(0)
        self._set_message(f"Скачивание '{name}': подготовка...", COLOR_ACCENT)
        self._refresh_all()
        self.app.set_download_active(True)

        worker = threading.Thread(
            target=self._download_worker, args=(name, url), daemon=True
        )
        worker.start()

    def _download_worker(self, name: str, url: str):
        """Работает в отдельном потоке: сеть и диск, но никаких виджетов."""
        try:
            download_whisper_model(
                url,
                WHISPER_CACHE_DIR,
                on_progress=lambda done, total: self._ui(
                    lambda: self._show_progress(name, done, total)
                ),
                on_status=lambda text: self._ui(
                    lambda: self._set_message(text, COLOR_ACCENT)
                ),
                is_cancelled=lambda: self._cancel_requested,
            )
        except DownloadCancelled:
            self._ui(lambda: self._after_cancel(name))
        except Exception as exc:  # noqa: BLE001 — нужен любой сбой сети/диска
            # Имя exc удаляется по выходе из блока except, а лямбда выполнится
            # позже в главном потоке, поэтому фиксируем его как аргумент.
            self._ui(lambda error=exc: self._after_error(name, error))
        else:
            self._ui(lambda: self._after_success(name))

    def _show_progress(self, name: str, done: int, total: int):
        if not self._alive or self._downloading != name:
            return
        if total > 0:
            fraction = min(done / total, 1.0)
            self.progress_bar.set(fraction)
            self._set_message(
                f"Скачивание '{name}': {fraction * 100:.0f}% "
                f"({format_bytes(done)} из {format_bytes(total)})",
                COLOR_ACCENT
            )
        else:
            # Сервер не сообщил размер — показываем хотя бы объём.
            self.progress_bar.set(0)
            self._set_message(f"Скачивание '{name}': {format_bytes(done)}", COLOR_ACCENT)

    def _after_success(self, name: str):
        """Скачивание завершено: перечитываем кэш и обновляем ОБА окна."""
        # Признак загрузки снимается только здесь, в главном потоке. Если
        # сбросить его в рабочем потоке, между сбросом и этим обновлением
        # успеет стартовать другая загрузка — и её кнопки разблокируются.
        self._downloading = None
        if not self._alive:
            return
        # refresh_whisper_models() заново вызывает _build_whisper_display_list(),
        # поэтому маркер в главном меню переключается с 🔵 на 🟢 автоматически.
        self.app.refresh_whisper_models()
        self._refresh_all()
        self.progress_bar.set(1.0)
        self._set_message(
            f"✓ Модель '{name}' скачана и доступна для транскрибации", "green"
        )
        self.app.set_download_active(False)

    def _after_error(self, name: str, exc: BaseException):
        self._downloading = None
        if not self._alive:
            return
        self._refresh_all()
        self.progress_bar.set(0)
        self._set_message(
            f"❌ Не удалось скачать '{name}': {describe_download_error(exc)}. "
            f"Недокачанный файл удалён, попробуйте ещё раз.",
            COLOR_RED
        )
        self.app.set_download_active(False)

    def _after_cancel(self, name: str):
        self._downloading = None
        if not self._alive:
            return
        self._refresh_all()
        self.progress_bar.set(0)
        self._set_message(f"Загрузка '{name}' отменена, временный файл удалён")
        self.app.set_download_active(False)

    # --- Закрытие окна ---

    def _on_close(self):
        if self._downloading is not None:
            # Не обрываем поток грубо: просим его остановиться и ждём очистки.
            self._cancel_requested = True
            self._set_message("Отмена загрузки...")
            self.after(100, self._wait_for_worker)
            return
        self._destroy()

    def _wait_for_worker(self):
        if not self._alive:
            return
        if self._downloading is not None:
            self.after(100, self._wait_for_worker)
            return
        self._destroy()

    def _destroy(self):
        self._alive = False
        self._cancel_pending_delete()
        if self.app.model_manager is self:
            self.app.model_manager = None
        self.app.set_download_active(False)
        self.destroy()


class NordScribeApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("NordScribe - Локальная Транскрибация")
        self.geometry("1100x700")

        # Кэширование модели Whisper (чтобы не загружать каждый раз)
        self.whisper_model: Optional[whisper.Whisper] = None

        # Сканируем кэш и строим подписи с цветными маркерами
        self.downloaded_whisper_models = get_downloaded_whisper_models()
        self.base_whisper_names = WHISPER_MODELS
        self.whisper_display_options = self._build_whisper_display_list()

        # Две РАЗНЫЕ переменные, и это принципиально:
        #   whisper_var         — чистое имя модели ("large"), идёт в load_model;
        #   whisper_display_var — подпись для виджета ("🟢 large").
        # CTkOptionMenu показывает текст, привязанный к своей variable, поэтому
        # если хранить в ней чистое имя, маркер в свёрнутом виде не виден.
        self.whisper_var = ctk.StringVar(value="base")
        self.whisper_display_var = ctk.StringVar(value=self._label_for("base"))

        # Переменные для записи с микрофона
        self.is_recording = False
        self.recording_thread = None
        self.audio_data = []
        self.sample_rate = 16000  # Частота дискретизации для Whisper
        self.record_duration = 0  # Длительность текущей записи в секундах

        # Диспетчер моделей и признак активной загрузки (блокирует кнопки)
        self.model_manager: Optional[ModelManagerWindow] = None
        self._download_active = False

        # Настройка сетки главного окна: одна колонка во всю ширину и 4 строки
        self.grid_columnconfigure(0, weight=1)  # Единственная колонка (Транскрибация)
        self.grid_rowconfigure(0, weight=0)     # Строка 0: Панель настроек (фиксировано)
        self.grid_rowconfigure(1, weight=0)     # Строка 1: Заголовок (фиксировано)
        self.grid_rowconfigure(2, weight=1)     # Строка 2: Текстовое поле (растягивается)
        self.grid_rowconfigure(3, weight=0)     # Строка 3: Панель управления (фиксировано)

        # === ВЕРХНЯЯ ПАНЕЛЬ НАСТРОЕК (СТРОКА 0) ===
        self.settings_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.settings_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 5))
        self.settings_frame.grid_columnconfigure(0, weight=0)
        self.settings_frame.grid_columnconfigure(1, weight=0)
        self.settings_frame.grid_columnconfigure(2, weight=1)

        # Метка и выбор модели Whisper
        ctk.CTkLabel(
            self.settings_frame,
            text="Модель Whisper:",
            font=ctk.CTkFont(size=13, weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))

        # Шрифт с цветными эмодзи обязателен: на шрифте по умолчанию
        # маркеры 🟢 и 🔵 рисуются одинаковыми серыми кружками
        # (проверено снимком открытого списка).
        emoji_family = pick_emoji_font_family()

        def menu_font():
            if emoji_family:
                return ctk.CTkFont(family=emoji_family, size=13)
            return ctk.CTkFont(size=13)

        self.whisper_menu = ctk.CTkOptionMenu(
            self.settings_frame,
            variable=self.whisper_display_var,
            values=self.whisper_display_options,
            command=self._on_whisper_model_change,
            width=140,
            height=30,
            font=menu_font(),
            dropdown_font=menu_font()
        )
        self.whisper_menu.grid(row=0, column=1, sticky="w")
        self._apply_menu_item_colors()

        # Кнопка вызова диспетчера моделей — правый верхний угол окна
        self.btn_manage_models = ctk.CTkButton(
            self.settings_frame,
            text="⚙ Управление моделями",
            command=self.open_model_manager,
            width=190,
            height=30,
            fg_color=COLOR_GREY,
            hover_color=COLOR_GREY_HOVER
        )
        self.btn_manage_models.grid(row=0, column=2, sticky="e")

        # === БЛОК ТРАНСКРИБАЦИИ ===
        # Заголовок (строка 1)
        self.lbl_transcript = ctk.CTkLabel(
            self,
            text="🎙️ Транскрибация (Whisper)",
            font=ctk.CTkFont(size=18, weight="bold")
        )
        self.lbl_transcript.grid(row=1, column=0, sticky="nw", padx=15, pady=(5, 10))

        # Текстовое поле вывода (строка 2, растягивается)
        self.txt_transcript = ctk.CTkTextbox(
            self,
            wrap="word",
            font=ctk.CTkFont(size=14)
        )
        self.txt_transcript.grid(row=2, column=0, sticky="nsew", padx=15, pady=(0, 15))

        # --- НИЖНИЙ КОНТЕЙНЕР (Кнопки + Прогресс) - СТРОКА 3 ---
        self.frame_controls = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_controls.grid(row=3, column=0, sticky="ew", padx=15, pady=(0, 15))
        self.frame_controls.grid_columnconfigure((0, 1), weight=1)

        # Кнопка загрузки аудио файла (синяя, стандартная)
        self.btn_load_audio = ctk.CTkButton(
            self.frame_controls,
            text="📁 Загрузить аудиофайл",
            command=self.start_transcription_thread,
            height=45,
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.btn_load_audio.grid(row=0, column=0, padx=(0, 10), pady=(0, 10), sticky="ew")

        # Кнопка записи с микрофона: красный цвет в покое, темно-красный при наведении
        self.btn_record_mic = ctk.CTkButton(
            self.frame_controls,
            text="🎤 Записать с микрофона",
            command=self.toggle_recording,
            fg_color=COLOR_RED,          # Красный цвет в покое
            hover_color=COLOR_RED_HOVER,  # Темно-красный при наведении
            height=45,
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.btn_record_mic.grid(row=0, column=1, padx=(10, 0), pady=(0, 10), sticky="ew")

        # Индикатор прогресса
        self.progress_bar = ctk.CTkProgressBar(self.frame_controls, height=10)
        self.progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 10))
        self.progress_bar.set(0)

        self.lbl_status = ctk.CTkLabel(
            self.frame_controls,
            text="Готов к работе",
            text_color="gray",
            font=ctk.CTkFont(size=13)
        )
        self.lbl_status.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 5))

    # --- ДИСПЕТЧЕР МОДЕЛЕЙ ---

    def open_model_manager(self):
        """Открывает диспетчер моделей; повторный клик поднимает уже открытое окно."""
        if self.model_manager is not None and self.model_manager.winfo_exists():
            self.model_manager.lift()
            self.model_manager.focus_force()
            return
        self.model_manager = ModelManagerWindow(self)

    def set_download_active(self, active: bool):
        """Блокирует элементы управления транскрибацией на время скачивания."""
        self._download_active = active
        self._apply_controls()

    def _apply_controls(self):
        """Приводит кнопки в соответствие с текущим состоянием загрузки."""
        state = "disabled" if self._download_active else "normal"
        self.btn_load_audio.configure(state=state)
        self.btn_record_mic.configure(state=state)
        self.whisper_menu.configure(state=state)

    def refresh_whisper_models(self):
        """
        Перечитывает кэш, перестраивает маркеры и обновляет подпись виджета.

        Вызывается после каждой успешной загрузки и удаления в диспетчере,
        поэтому маркер в главном окне переключается автоматически.
        """
        self.downloaded_whisper_models = get_downloaded_whisper_models()
        selected = self._clean_name(self.whisper_var.get())
        if selected not in self.base_whisper_names:
            selected = self.base_whisper_names[0]

        self.whisper_display_options = self._build_whisper_display_list()
        self.whisper_menu.configure(values=self.whisper_display_options)
        # configure(values=...) пересобирает пункты меню, поэтому цвета
        # нужно накладывать заново после каждого обновления списка.
        self._apply_menu_item_colors()
        self.whisper_var.set(selected)
        self.whisper_display_var.set(self._label_for(selected))

    # --- ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ДЛЯ WHISPER MENU ---

    def _clean_name(self, display_name: str) -> str:
        """
        Снимает цветной маркер и возвращает чистое имя модели для Whisper.
        Примеры: '🟢 tiny' -> 'tiny', '🔵 small' -> 'small'.
        """
        name = (display_name or "").strip()
        for marker in _DISPLAY_MARKERS:
            if name.startswith(marker):
                name = name[len(marker):].strip()
                break
        # Берём только первое слово: отсекает любые пояснения после имени,
        # например устаревшее 'tiny (Доступна)'.
        parts = name.split()
        return parts[0] if parts else ""

    def _build_whisper_display_list(self) -> List[str]:
        """
        Строит подписи для выпадающего списка с цветными маркерами статуса:
        '🟢 tiny' для скачанных и '🔵 small' для отсутствующих в кэше.
        """
        options = []
        for model in self.base_whisper_names:
            if model in self.downloaded_whisper_models:
                options.append(f"{MARKER_DOWNLOADED} {model}")
            else:
                options.append(f"{MARKER_MISSING} {model}")
        return options

    def _label_for(self, model_name: str) -> str:
        """Возвращает каноническую подпись с маркером для указанной модели."""
        for option in self.whisper_display_options:
            if self._clean_name(option) == model_name:
                return option
        return model_name

    def _apply_menu_item_colors(self) -> None:
        """
        Красит пункты выпадающего списка по отдельности: зелёный для
        скачанных моделей, синий для отсутствующих.

        Цветные эмодзи рисуются не везде — это зависит от сборки Tk и от
        доступного шрифта. Цвет самого текста работает всегда, поэтому
        статус модели читается даже при монохромных маркерах.

        DropdownMenu в CustomTkinter — наследник tkinter.Menu, поэтому
        цвет задаётся через entryconfigure(foreground=...) для каждой строки.
        """
        menu = getattr(self.whisper_menu, "_dropdown_menu", None)
        if menu is None:
            return
        for index, model in enumerate(self.base_whisper_names):
            color = (MENU_COLOR_DOWNLOADED if model in self.downloaded_whisper_models
                     else MENU_COLOR_MISSING)
            try:
                menu.entryconfigure(index, foreground=color)
            except tk.TclError:
                break  # в меню меньше пунктов, чем подписей

    def _extract_whisper_model_name(self, display_string: str) -> str:
        """
        Извлекает чистое имя модели из выбранной строки меню.
        Отдельный метод, чтобы очистку можно было переиспользовать и тестировать.
        """
        return self._clean_name(display_string)

    def _on_whisper_model_change(self, choice: str):
        """
        Обработчик выбора в меню. Принимает строку с маркером ('🟢 tiny'),
        сохраняет чистое имя для load_model и каноническую подпись для виджета.
        """
        clean_name = self._extract_whisper_model_name(choice)
        self.whisper_var.set(clean_name)
        # Виджет должен показывать подпись с маркером, а не голое имя.
        self.whisper_display_var.set(self._label_for(clean_name))

        is_downloaded = clean_name in self.downloaded_whisper_models
        if is_downloaded:
            self.lbl_status.configure(
                text=f"Выбрана модель '{clean_name}' (Локально)", text_color="green"
            )
        else:
            self.lbl_status.configure(
                text=f"Выбрана модель '{clean_name}' (Будет загружена при использовании)",
                text_color="orange"
            )

    # --- ЛОГИКА МНОГОПОТОЧНОСТИ ---

    def load_whisper_model(self):
        """Загружает модель Whisper при первом запуске с выбранной в UI моделью"""
        if self.whisper_model is None:
            # Из переменной UI приходит подпись с маркером — снимаем его.
            selected_model = self._extract_whisper_model_name(self.whisper_var.get())
            if selected_model not in self.base_whisper_names:
                self.lbl_status.configure(
                    text=f"Неизвестная модель: '{selected_model}'", text_color="red"
                )
                return False

            self.lbl_status.configure(text=f"Загрузка модели Whisper ({selected_model})...")
            self.update()  # Принудительное обновление UI перед загрузкой
            try:
                # Загрузка модели (кэшируется после первого раза в ~/.cache/whisper/)
                self.whisper_model = whisper.load_model(selected_model)
                self.lbl_status.configure(text="Модель готова к работе")
                return True
            except Exception as e:
                self.lbl_status.configure(text=f"Ошибка загрузки модели: {str(e)}")
                return False
        return True

    def start_transcription_thread(self):
        """Запускает тяжелую задачу Whisper в отдельном потоке"""
        # Сначала загружаем модель, если еще не загружена
        if not self.load_whisper_model():
            return

        # Диалог выбора файла
        file_path = filedialog.askopenfilename(
            title="Выберите аудиофайл",
            filetypes=[
                ("Аудио файлы", "*.mp3 *.wav *.ogg *.flac *.m4a"),
                ("Все файлы", "*.*")
            ]
        )

        if not file_path:
            self._apply_controls()
            self.lbl_status.configure(text="Файл не выбран")
            return

        self.btn_load_audio.configure(state="disabled")
        self.lbl_status.configure(text=f"Обработка: {os.path.basename(file_path)}")
        self.progress_bar.set(0)

        # Очистка предыдущего результата
        self.txt_transcript.delete("1.0", "end")

        # Запуск транскрибации в отдельном потоке
        thread = threading.Thread(target=self.run_whisper_transcription, args=(file_path,), daemon=True)
        thread.start()

    def toggle_recording(self):
        """Переключает состояние записи с микрофона"""
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        """Начинает запись с микрофона в отдельном потоке"""
        if not self.load_whisper_model():
            return

        self.is_recording = True
        self.audio_data = []
        self.record_duration = 0

        # Обновление UI кнопки: меняем текст и цвет на более темный красный
        self.btn_record_mic.configure(
            text="⏹ Остановить запись",
            fg_color=COLOR_RED_HOVER,     # Темнее при записи
            hover_color="#8E0000"
        )
        self.lbl_status.configure(text="🔴 Запись идет... Нажмите 'Остановить запись' когда закончите", text_color=COLOR_RED)
        self.txt_transcript.delete("1.0", "end")

        # Запуск потока записи
        self.recording_thread = threading.Thread(target=self.record_audio, daemon=True)
        self.recording_thread.start()

    def record_audio(self):
        """Функция записи аудио в цикле"""
        try:
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype='int16',
                callback=self.audio_callback
            )

            with stream:
                while self.is_recording:
                    time.sleep(0.1)
                    self.record_duration += 0.1
                    # Обновляем статус каждую секунду
                    if int(self.record_duration) % 1 == 0:
                        self.update_status(f"Запись идет... {int(self.record_duration)} сек")

        except Exception as e:
            self.update_status(f"Ошибка записи: {str(e)}")
            self.is_recording = False

    def audio_callback(self, indata, frames, time_info, status):
        """Коллбэк для захвата аудио данных"""
        if self.is_recording:
            self.audio_data.append(indata.copy())

    def stop_recording(self):
        """Останавливает запись и начинает транскрибацию"""
        self.is_recording = False

        # Возврат кнопки в исходное красное состояние
        self.btn_record_mic.configure(
            text="🎤 Записать с микрофона",
            fg_color=COLOR_RED,
            hover_color=COLOR_RED_HOVER
        )
        self.lbl_status.configure(text="Обработка записанного аудио...", text_color="gray")

        if self.audio_data:
            # Сохраняем записанное аудио во временный файл
            try:
                audio_data = np.concatenate(self.audio_data, axis=0)

                # Создаем временный WAV файл
                temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
                temp_path = temp_file.name
                temp_file.close()

                # Записываем WAV файл
                write(temp_path, self.sample_rate, audio_data)

                # Запускаем транскрибацию временного файла
                thread = threading.Thread(target=self.run_whisper_transcription, args=(temp_path, True), daemon=True)
                thread.start()

            except Exception as e:
                self.update_status(f"Ошибка сохранения аудио: {str(e)}")
                self.btn_record_mic.configure(state="normal")
        else:
            self.update_status("Аудио не записано")
            self.btn_record_mic.configure(state="normal")

    def run_whisper_transcription(self, file_path: str, is_temp_file: bool = False):
        """Реальная транскрибация аудио с помощью Whisper"""
        try:
            # Whisper не предоставляет детальный прогресс для коротких файлов,
            # но мы можем обновлять статус во время обработки
            self.update_status("Начинаю распознавание...")

            # Выполняем транскрибацию
            # verbose=None показывает прогресс в консоли, но мы используем свой UI
            result = self.whisper_model.transcribe(
                file_path,
                language="ru",  # Можно сделать настраиваемым параметром
                task="transcribe",
                verbose=False
            )

            # Получаем текст из результата
            transcribed_text = result.get("text", "").strip()

            # Обновляем прогресс до 100%
            self.update_progressbar(1.0)

            # Вставляем результат в текстовое поле
            self.update_transcript_result(transcribed_text)

            # Удаляем временный файл, если это запись с микрофона
            if is_temp_file and os.path.exists(file_path):
                try:
                    os.unlink(file_path)
                except Exception as e:
                    print(f"Не удалось удалить временный файл: {e}")

        except Exception as e:
            self.update_status(f"Ошибка транскрибации: {str(e)}")
        finally:
            # Не разблокируем кнопки, если параллельно идёт скачивание модели.
            self._apply_controls()

    # --- МЕТОДЫ ОБНОВЛЕНИЯ UI (Безопасный вызов из рабочих потоков) ---
    # Виджеты Tkinter можно изменять только из главного потока,
    # поэтому все обновления планируются через self.after(0, ...).

    def update_progressbar(self, value):
        self.after(0, lambda: self.progress_bar.set(value))

    def update_transcript_result(self, text):
        self.after(0, lambda: self.txt_transcript.insert("1.0", text))
        self.after(0, lambda: self.lbl_status.configure(text="Готово"))
        self.after(0, lambda: self.progress_bar.set(1))

    def update_status(self, message):
        self.after(0, lambda: self.lbl_status.configure(text=message))


if __name__ == "__main__":
    app = NordScribeApp()
    app.mainloop()
