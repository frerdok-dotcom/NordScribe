"""Model catalog + cache + downloader in one service.

The UI should not deal with HTTP, files or SHA-256 directly. :class:`ModelService`
is the single entry point it needs: list models, check what is installed,
download and delete weights, and turn errors into messages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from nordscribe.core.errors import UnknownModelError
from nordscribe.core.models import MODEL_SPECS, MODEL_SPECS_BY_NAME, ModelSpec
from nordscribe.infra.cache import ModelCache, RemovedFile
from nordscribe.services.downloader import (
    CancelCallback,
    ProgressCallback,
    StatusCallback,
    describe_download_error,
    download_file,
)

__all__ = ["DeleteResult", "ModelService"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeleteResult:
    """Outcome of removing a model's files."""

    removed: tuple[RemovedFile, ...]
    still_available: bool

    @property
    def freed_bytes(self) -> int:
        """Total size of the removed files."""
        return sum(item.size for item in self.removed)

    @property
    def removed_names(self) -> tuple[str, ...]:
        """File names that were removed."""
        return tuple(item.path.name for item in self.removed)


class ModelService:
    """High-level operations on Whisper model files."""

    def __init__(self, cache: ModelCache | None = None) -> None:
        self._cache = cache or ModelCache()

    @property
    def cache_dir(self) -> Path:
        """Directory with the weights (used by environment checks)."""
        return self._cache.directory

    @property
    def specs(self) -> tuple[ModelSpec, ...]:
        """Models shown in the UI, in menu order."""
        return MODEL_SPECS

    def spec(self, name: str) -> ModelSpec | None:
        """Return the catalog entry for ``name``, or ``None``."""
        return MODEL_SPECS_BY_NAME.get(name)

    def require_spec(self, name: str) -> ModelSpec:
        """Return the catalog entry or raise :class:`UnknownModelError`."""
        spec = self.spec(name)
        if spec is None:
            raise UnknownModelError(f"неизвестная модель: {name}")
        return spec

    def downloaded(self) -> set[str]:
        """Names of models whose weights are present in the cache."""
        return self._cache.downloaded()

    def size_of(self, name: str) -> int | None:
        """Size of the cached weights in bytes, if present."""
        spec = self.spec(name)
        return self._cache.size_of(spec) if spec is not None else None

    def download(
        self,
        name: str,
        *,
        on_progress: ProgressCallback | None = None,
        on_status: StatusCallback | None = None,
        is_cancelled: CancelCallback | None = None,
    ) -> None:
        """Download the weights for ``name``.

        Raises:
            UnknownModelError: the name is not in the catalog.
            nordscribe.core.errors.DownloadError: network/checksum failure.
        """
        spec = self.require_spec(name)
        logger.info("downloading model %s from %s", name, spec.url)
        download_file(
            spec.url,
            self._cache.directory,
            expected_sha256=spec.sha256,
            on_progress=on_progress,
            on_status=on_status,
            is_cancelled=is_cancelled,
        )

    def delete(self, name: str) -> DeleteResult:
        """Delete the weights and an unfinished download for ``name``."""
        spec = self.require_spec(name)
        removed = tuple(self._cache.remove(spec))
        still_available = name in self._cache.downloaded()
        logger.info(
            "deleted model %s: removed=%s, still_available=%s",
            name,
            [item.path.name for item in removed],
            still_available,
        )
        return DeleteResult(removed=removed, still_available=still_available)

    @staticmethod
    def describe_error(exc: BaseException) -> str:
        """Return a user-facing message for a download failure."""
        return describe_download_error(exc)
