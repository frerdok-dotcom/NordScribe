"""HTTP transfer of Whisper model files.

The downloader knows about HTTP range requests, ``.part`` files and SHA-256
verification. It does not know which models exist and it does not import
``whisper``; the model catalog and the cache live in their own modules.
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from nordscribe.core.errors import (
    ChecksumError,
    DownloadCancelled,
    DownloadError,
    IncompleteDownload,
)
from nordscribe.core.formatting import format_bytes
from nordscribe.infra.hashing import sha256_of_file

__all__ = [
    "PROGRESS_INTERVAL",
    "describe_download_error",
    "download_file",
]

logger = logging.getLogger(__name__)

#: Progress callback: (received bytes, total bytes; 0 when unknown).
ProgressCallback = Callable[[int, int], None]
#: Human-readable phase, e.g. "Проверка целостности файла...".
StatusCallback = Callable[[str], None]
#: Returns ``True`` when the operation should be aborted.
CancelCallback = Callable[[], bool]

_CHUNK_SIZE = 256 * 1024

#: At most ten UI updates per second; otherwise a fast connection floods the
#: Tk event queue with hundreds of progress events.
PROGRESS_INTERVAL = 0.1


def _parse_filename_and_digest(url: str) -> tuple[str, str | None]:
    """Extract the file name and SHA-256 from a Whisper model URL."""
    segments = urllib.parse.urlsplit(url).path.strip("/").split("/")
    filename = segments[-1] if segments else ""
    if not filename:
        raise DownloadError(f"URL has no file name: {url}")
    digest = segments[-2] if len(segments) >= 2 else None
    if digest is not None and (
        len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest)
    ):
        digest = None
    return filename, digest


def _parse_total(headers: object) -> int:
    """Read the total size from Content-Range, falling back to Content-Length."""
    content_range = ""
    content_length = ""
    get = getattr(headers, "get", None)
    if callable(get):
        content_range = str(get("Content-Range") or "")
        content_length = str(get("Content-Length") or "")
    if "/" in content_range:
        tail = content_range.rsplit("/", 1)[-1].strip()
        if tail.isdigit():
            return int(tail)
    return int(content_length) if content_length.isdigit() else 0


def download_file(
    url: str,
    dest_dir: Path,
    *,
    expected_sha256: str | None = None,
    on_progress: ProgressCallback | None = None,
    on_status: StatusCallback | None = None,
    is_cancelled: CancelCallback | None = None,
    chunk_size: int = _CHUNK_SIZE,
    progress_interval: float = PROGRESS_INTERVAL,
    allow_resume: bool = True,
) -> Path:
    """Download one file into ``dest_dir`` with resume and verification.

    Bytes are written to ``<filename>.part`` and moved to the final name only
    after the SHA-256 check. On a network failure the ``.part`` file is kept so
    the next attempt can continue; on a checksum error, cancellation or an
    explicit HTTP error it is removed.

    Args:
        url: direct file URL (``.../<sha256>/<filename>``).
        dest_dir: target directory.
        expected_sha256: digest to verify; parsed from ``url`` when omitted.
        on_progress: throttled progress callback.
        on_status: phase messages.
        is_cancelled: checked between chunks.
        chunk_size: read size.
        progress_interval: minimum seconds between progress calls.
        allow_resume: continue an existing ``.part`` file.

    Returns:
        Path to the downloaded file.

    Raises:
        DownloadCancelled: the caller cancelled.
        ChecksumError: the bytes do not match the digest.
        IncompleteDownload: the connection ended before the expected size.
        DownloadError: the URL is unusable.
        urllib.error.HTTPError: the server returned an error status.
        urllib.error.URLError: the server is unreachable.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    filename, url_digest = _parse_filename_and_digest(url)
    digest = expected_sha256 or url_digest
    if digest is not None and len(digest) != 64:
        digest = None

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

    def emit_progress(received: int, total: int, *, force: bool = False) -> None:
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
                # The server ignored Range: restart from zero and drop the part.
                resume_from = 0
                part.unlink(missing_ok=True)

            if resume_from:
                total = _parse_total(response.headers)
            else:
                total = int(response.headers.get("Content-Length") or 0)

            received = resume_from
            emit_progress(received, total, force=True)

            with part.open("ab" if resume_from else "wb") as handle:
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
    except (DownloadCancelled, ChecksumError, urllib.error.HTTPError):
        part.unlink(missing_ok=True)
        raise
    except BaseException:
        # Network failure: keep .part for the next attempt.
        raise

    part.replace(dest)
    return dest


def describe_download_error(exc: BaseException) -> str:
    """Turn a download exception into a short message for the status bar."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"сервер вернул ошибку {exc.code} ({exc.reason})"
    if isinstance(exc, urllib.error.URLError):
        return f"нет соединения с сервером ({exc.reason})"
    if isinstance(exc, (ChecksumError, IncompleteDownload, DownloadCancelled)):
        return str(exc)
    if isinstance(exc, TimeoutError):
        return "превышено время ожидания ответа сервера"
    text = str(exc).strip()
    return text if text else exc.__class__.__name__
