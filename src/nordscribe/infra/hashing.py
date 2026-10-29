"""SHA-256 helpers for model files."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from nordscribe.core.errors import DownloadCancelled

__all__ = ["sha256_of_file"]

_HASH_CHUNK_SIZE = 1024 * 1024


def sha256_of_file(
    path: Path,
    chunk_size: int = _HASH_CHUNK_SIZE,
    is_cancelled: Callable[[], bool] | None = None,
) -> str:
    """Hash a file in chunks without loading it into memory.

    A 2.9 GB ``large`` file takes a few seconds, which is acceptable before
    loading the model because it turns an obscure torch error into a clear
    "file was repaired" message.

    Raises:
        DownloadCancelled: ``is_cancelled`` returned ``True``.
        OSError: the file cannot be read.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            if is_cancelled is not None and is_cancelled():
                raise DownloadCancelled()
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
