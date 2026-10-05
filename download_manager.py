"""
Сетевой слой NordScribe.

Модуль отвечает за модели Whisper как за файлы: адреса на серверах OpenAI,
контрольные суммы, скачивание с докачкой и отчётом о прогрессе, проверку
целостности уже лежащих в кэше весов и сканирование кэша.

Здесь нет ни одной зависимости от графического интерфейса и ни одного
порождения потоков: все callback-и вызываются синхронно, в том потоке,
из которого вызвана функция. Переключать их в главный поток Tkinter
обязан UI-слой.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

import whisper

__all__ = [
    "WHISPER_CACHE_DIR",
    "WHISPER_MODELS",
    "WHISPER_MODEL_SIZES",
    "ChecksumError",
    "DownloadCancelled",
    "IncompleteDownload",
    "cached_model_is_valid",
    "describe_download_error",
    "download_whisper_model",
    "format_bytes",
    "get_downloaded_whisper_models",
    "get_model_url",
    "model_file_path",
    "remove_model_file",
    "sha256_of_file",
]

logger = logging.getLogger(__name__)

#: Прогресс скачивания: (получено байт, всего байт). total == 0, если размер
#: неизвестен.
ProgressCallback = Callable[[int, int], None]
#: Текстовая фаза операции, например «Проверка целостности файла...».
StatusCallback = Callable[[str], None]
#: Возвращает True, если операцию нужно прервать.
CancelCallback = Callable[[], bool]

#: Папка, в которую Whisper складывает веса моделей по умолчанию.
#: Именно отсюда их читает whisper.load_model(), поэтому качать нужно строго сюда.
WHISPER_CACHE_DIR: Path = Path.home() / ".cache" / "whisper"

#: Модели, доступные в интерфейсе приложения.
WHISPER_MODELS: Sequence[str] = ("tiny", "base", "small", "medium", "large")

#: Примерный вес файлов на диске — для моделей, которых ещё нет в кэше.
WHISPER_MODEL_SIZES: dict[str, str] = {
    "tiny": "~75 МБ",
    "base": "~140 МБ",
    "small": "~460 МБ",
    "medium": "~1.5 ГБ",
    "large": "~2.9 ГБ",
}

#: Резервные адреса на случай, если приватный словарь whisper._MODELS
#: исчезнет или переименуется в новой версии библиотеки.
#:
#: ВНИМАНИЕ: последний сегмент пути — это имя файла на диске (для "large"
#: это large-v3.pt!), а предпоследний — ожидаемая контрольная сумма SHA-256.
_FALLBACK_MODEL_URLS: dict[str, str] = {
    "tiny": (
        "https://openaipublic.azureedge.net/main/whisper/models/"
        "65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9/tiny.pt"
    ),
    "base": (
        "https://openaipublic.azureedge.net/main/whisper/models/"
        "ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e/base.pt"
    ),
    "small": (
        "https://openaipublic.azureedge.net/main/whisper/models/"
        "9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794/small.pt"
    ),
    "medium": (
        "https://openaipublic.azureedge.net/main/whisper/models/"
        "345ae4da62f9b3d59415adc60127b97c714f32e89e936602e85993674d08dcb1/medium.pt"
    ),
    "large": (
        "https://openaipublic.azureedge.net/main/whisper/models/"
        "e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb/large-v3.pt"
    ),
}

_CHUNK_SIZE = 256 * 1024
_HASH_CHUNK_SIZE = 1024 * 1024
#: Не чаще 10 обновлений прогресса в секунду: иначе при быстром канале
#: в очередь событий Tkinter улетают сотни обновлений в секунду.
PROGRESS_INTERVAL = 0.1


class DownloadCancelled(Exception):
    """Загрузка прервана пользователем (например, закрытием окна диспетчера)."""


class ChecksumError(Exception):
    """Скачанный файл повреждён: SHA-256 не совпала с ожидаемой."""


class IncompleteDownload(OSError):
    """Соединение закрылось раньше времени, файл недополучен."""


def get_model_url(model_name: str) -> str | None:
    """
    Возвращает прямой URL файла модели на серверах OpenAI.

    Источник истины — словарь ``whisper._MODELS``: он гарантированно совпадает
    с тем, что библиотека ожидает найти в кэше. Если приватный словарь
    недоступен, используется резервная таблица.
    """
    urls = getattr(whisper, "_MODELS", None)
    if isinstance(urls, dict) and urls.get(model_name):
        return urls[model_name]
    return _FALLBACK_MODEL_URLS.get(model_name)


def expected_sha256(model_name: str) -> str | None:
    """Возвращает ожидаемую SHA-256 модели, зашитую в её URL."""
    url = get_model_url(model_name)
    if not url:
        return None
    segments = urllib.parse.urlsplit(url).path.strip("/").split("/")
    digest = segments[-2] if len(segments) >= 2 else None
    return digest if digest and len(digest) == 64 else None


def model_file_path(model_name: str, cache_dir: Path | None = None) -> Path | None:
    """
    Возвращает путь к файлу весов модели в кэше.

    Имя файла берётся из URL, а не из имени модели: для "large" это
    ``large-v3.pt``. Если сохранить его как ``large.pt``, то
    ``whisper.load_model()`` файл не найдёт и начнёт скачивание заново.
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


def sha256_of_file(
    path: Path,
    chunk_size: int = _HASH_CHUNK_SIZE,
    is_cancelled: CancelCallback | None = None,
) -> str:
    """
    Считает SHA-256 файла блоками, не загружая его целиком в память.

    Замер на этой машине: ~1 ГБ/с, то есть проверка `large` (2.9 ГБ) стоит
    около трёх секунд — приемлемо, чтобы делать её перед загрузкой модели.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            if is_cancelled is not None and is_cancelled():
                raise DownloadCancelled()
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def cached_model_is_valid(
    model_name: str,
    cache_dir: Path | None = None,
    is_cancelled: CancelCallback | None = None,
) -> bool | None:
    """
    Проверяет целостность уже лежащего в кэше файла модели.

    Нужна, чтобы обрезанный или испорченный файл не выглядел как рабочий:
    ``whisper.load_model()`` на битом файле падает с невнятной ошибкой torch
    вместо понятного сообщения.

    Returns:
        True — файл есть и хеш совпал;
        False — файл есть, но повреждён;
        None — файла нет или проверить нечем.
    """
    path = model_file_path(model_name, cache_dir)
    digest = expected_sha256(model_name)
    if path is None or digest is None or not path.is_file():
        return None
    actual = sha256_of_file(path, is_cancelled=is_cancelled)
    return actual == digest


def remove_model_file(model_name: str, cache_dir: Path | None = None) -> list[str]:
    """
    Удаляет веса модели и недокачанный ``.part`` из кэша.

    Returns:
        Имена реально удалённых файлов (пустой список, если удалять было нечего).
    """
    path = model_file_path(model_name, cache_dir)
    if path is None:
        return []
    removed: list[str] = []
    for target in (path, path.with_name(path.name + ".part")):
        try:
            if target.exists():
                target.unlink()
                removed.append(target.name)
        except OSError as exc:
            logger.warning("не удалось удалить %s: %s", target, exc)
    return removed


def download_whisper_model(
    url: str,
    dest_dir: Path,
    on_progress: ProgressCallback | None = None,
    on_status: StatusCallback | None = None,
    is_cancelled: CancelCallback | None = None,
    chunk_size: int = _CHUNK_SIZE,
    progress_interval: float = PROGRESS_INTERVAL,
    allow_resume: bool = True,
) -> Path:
    """
    Скачивает файл модели в ``dest_dir``, сообщая реальный прогресс.

    Имя файла и контрольная сумма берутся из самого URL вида
    ``https://.../<sha256>/<filename>.pt`` — ровно так же, как это делает
    ``whisper._download()``, поэтому скачанный файл библиотека примет как свой.

    Данные пишутся во временный ``<filename>.part`` и переименовываются в
    целевой файл только после успешной проверки SHA-256.

    **Докачка.** Если ``.part`` уже существует, запрос уходит с заголовком
    ``Range`` и загрузка продолжается с места обрыва. Сервер моделей OpenAI
    это поддерживает (проверено: отвечает ``206`` с ``Content-Range``).
    Если сервер проигнорирует ``Range`` и вернёт ``200``, файл начнётся заново.

    **Что остаётся на диске при ошибке.** Сетевой сбой — ``.part`` сохраняется,
    чтобы следующая попытка докачала его. Повреждение контрольной суммы,
    отмена пользователем и явный отказ сервера — ``.part`` удаляется.

    Args:
        url: прямой адрес файла модели.
        dest_dir: каталог кэша.
        on_progress: вызывается не чаще ``progress_interval`` секунд.
        on_status: текстовые фазы операции.
        is_cancelled: проверяется между блоками.
        chunk_size: размер блока чтения.
        progress_interval: минимальный интервал между отчётами, секунды.
        allow_resume: разрешить докачку существующего ``.part``.

    Returns:
        Путь к скачанному файлу.

    Raises:
        DownloadCancelled: операция прервана через ``is_cancelled``.
        ChecksumError: содержимое не совпало с SHA-256 из URL.
        IncompleteDownload: соединение закрылось раньше времени.
        urllib.error.HTTPError: сервер вернул код ошибки.
        urllib.error.URLError: нет соединения с сервером.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    segments = urllib.parse.urlsplit(url).path.strip("/").split("/")
    filename = segments[-1]
    digest = segments[-2] if len(segments) >= 2 else None
    if digest is not None and len(digest) != 64:
        digest = None  # не похоже на hex-дайджест — проверять нечем

    dest = dest_dir / filename
    part = dest_dir / f"{filename}.part"

    resume_from = 0
    if allow_resume and part.is_file():
        resume_from = part.stat().st_size

    def emit_status(text: str) -> None:
        if on_status is not None:
            on_status(text)

    def check_cancel() -> None:
        if is_cancelled is not None and is_cancelled():
            raise DownloadCancelled()

    last_emit = 0.0

    def emit_progress(received: int, total: int, force: bool = False) -> None:
        nonlocal last_emit
        now = time.monotonic()
        if not force and now - last_emit < progress_interval:
            return
        last_emit = now
        if on_progress is not None:
            on_progress(received, total)

    if resume_from:
        emit_status(f"Докачка с {format_bytes(resume_from)}...")
    else:
        part.unlink(missing_ok=True)
        emit_status("Подключение к серверу...")

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "NordScribe"})
        if resume_from:
            request.add_header("Range", f"bytes={resume_from}-")

        with urllib.request.urlopen(request, timeout=30) as response:
            status = getattr(response, "status", 200)
            if resume_from and status != 206:
                # Сервер не умеет Range — начинаем заново.
                resume_from = 0
                part.unlink(missing_ok=True)

            if resume_from:
                content_range = response.headers.get("Content-Range") or ""
                total = int(content_range.rsplit("/", 1)[-1]) if "/" in content_range else 0
            else:
                total = int(response.headers.get("Content-Length") or 0)

            received = resume_from
            emit_progress(received, total, force=True)

            with open(part, "ab" if resume_from else "wb") as handle:
                while True:
                    check_cancel()
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    emit_progress(received, total)

            emit_progress(received, total, force=True)

        if total and received < total:
            raise IncompleteDownload(
                f"соединение прервано: получено {format_bytes(received)} "
                f"из {format_bytes(total)} (файл сохранён, загрузка продолжится)"
            )

        if digest is not None:
            emit_status("Проверка целостности файла...")
            check_cancel()
            actual = sha256_of_file(part, is_cancelled=is_cancelled)
            if actual != digest:
                raise ChecksumError("контрольная сумма не совпала — файл повреждён при загрузке")
    except DownloadCancelled:
        part.unlink(missing_ok=True)
        raise
    except ChecksumError:
        part.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError:
        # Сервер явно отказал — докачивать нечего.
        part.unlink(missing_ok=True)
        raise
    except BaseException:
        # Сетевой сбой: .part оставляем, следующая попытка продолжит загрузку.
        raise

    os.replace(part, dest)
    return dest


def describe_download_error(exc: BaseException) -> str:
    """Превращает исключение сетевого слоя в понятное пользователю сообщение."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"сервер вернул ошибку {exc.code} ({exc.reason})"
    if isinstance(exc, urllib.error.URLError):
        return f"нет соединения с сервером ({exc.reason})"
    if isinstance(exc, (ChecksumError, IncompleteDownload)):
        return str(exc)
    if isinstance(exc, TimeoutError):
        return "превышено время ожидания ответа сервера"
    text = str(exc).strip()
    return text if text else exc.__class__.__name__


def get_downloaded_whisper_models(
    cache_dir: Path | None = None,
) -> set[str]:
    """
    Сканирует кэш и возвращает множество имён доступных моделей.

    Проверяется только наличие файла, без чтения и хеширования: сканирование
    выполняется на каждое обновление интерфейса. Целостность конкретной
    модели проверяется отдельно, функцией :func:`cached_model_is_valid`.

    Файл ``large-v3.pt`` учитывается и как ``large-v3``, и как ``large`` —
    именно под именем ``large`` его запрашивает ``whisper.load_model()``.
    """
    cache_dir = Path(cache_dir or WHISPER_CACHE_DIR)
    official_models = (
        "tiny",
        "tiny.en",
        "base",
        "base.en",
        "small",
        "small.en",
        "medium",
        "medium.en",
        "large",
        "large-v1",
        "large-v2",
        "large-v3",
        "turbo",
    )
    downloaded: set[str] = set()

    if not cache_dir.exists():
        return downloaded

    try:
        files = list(cache_dir.glob("*.pt"))
    except PermissionError:
        return downloaded

    for file_path in files:
        model_key = file_path.name[:-3]  # убираем расширение .pt
        for official_name in official_models:
            if model_key == official_name:
                downloaded.add(official_name)
                # Для large-* версий добавляем и базовое имя 'large'.
                if official_name.startswith("large"):
                    downloaded.add("large")
                break

    return downloaded
