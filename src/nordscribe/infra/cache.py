"""Whisper model cache: scanning, validation and removal.

The cache is the directory Whisper itself uses (``~/.cache/whisper``). This
module only manipulates files; it never downloads anything and never imports
``whisper`` or ``torch``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nordscribe.core.errors import DownloadCancelled
from nordscribe.core.models import ModelSpec, aliases_for_filename
from nordscribe.infra.hashing import sha256_of_file
from nordscribe.infra.paths import WHISPER_CACHE_DIR

__all__ = ["ModelCache", "RemovedFile"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RemovedFile:
    """A cache file removed by :meth:`ModelCache.remove`."""

    path: Path
    size: int


class ModelCache:
    """Filesystem view of Whisper's model cache."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._dir = Path(cache_dir or WHISPER_CACHE_DIR)

    @property
    def directory(self) -> Path:
        """Directory that contains the ``*.pt`` files."""
        return self._dir

    def file_path(self, spec: ModelSpec) -> Path:
        """Path of the weights file for ``spec``."""
        return self._dir / spec.filename

    def part_path(self, spec: ModelSpec) -> Path:
        """Path of an unfinished download for ``spec``."""
        return self._dir / f"{spec.filename}.part"

    def downloaded(self) -> set[str]:
        """Return model names whose ``*.pt`` files exist.

        Only file names are checked; hashing happens in :meth:`is_valid` and is
        deliberately not part of this fast, UI-facing scan.
        """
        if not self._dir.exists():
            return set()
        try:
            files = list(self._dir.glob("*.pt"))
        except OSError as exc:
            logger.warning("cannot scan model cache %s: %s", self._dir, exc)
            return set()

        result: set[str] = set()
        for path in files:
            result.update(aliases_for_filename(path.name))
        return result

    def size_of(self, spec: ModelSpec) -> int | None:
        """Size of the weights file, or ``None`` when it is absent."""
        path = self.file_path(spec)
        try:
            return path.stat().st_size if path.is_file() else None
        except OSError:
            return None

    def is_valid(
        self,
        spec: ModelSpec,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> bool | None:
        """Check the SHA-256 of a cached file.

        Returns:
            ``True`` — the file exists and matches;
            ``False`` — the file exists but is corrupt;
            ``None`` — the file is absent, unreadable or hashing was cancelled.
        """
        path = self.file_path(spec)
        if not path.is_file():
            return None
        try:
            actual = sha256_of_file(path, is_cancelled=is_cancelled)
        except DownloadCancelled:
            logger.info("hashing of %s was cancelled", path)
            return None
        except OSError as exc:
            logger.warning("cannot hash %s: %s", path, exc)
            return None
        return actual == spec.sha256

    def remove(self, spec: ModelSpec) -> list[RemovedFile]:
        """Remove the weights file and any ``.part`` file.

        Returns:
            Files that were actually removed, with their sizes. Missing files
            and permission errors are reported in the log, not raised.
        """
        removed: list[RemovedFile] = []
        for target in (self.file_path(spec), self.part_path(spec)):
            try:
                if not target.exists():
                    continue
                size = target.stat().st_size
                target.unlink()
            except OSError as exc:
                logger.warning("cannot remove %s: %s", target, exc)
                continue
            removed.append(RemovedFile(path=target, size=size))
        return removed
