"""Shared helpers for the NordScribe test suite.

Temporary directories use the system temp folder, but are created with
``Path.mkdir`` rather than ``tempfile.mkdtemp``. On Windows this sandbox
assigns a restrictive ACL to ``mkdtemp`` directories, so files cannot be
written inside them; a plain directory inherits the normal temp permissions.
Process-wide ``TEMP`` is not modified, unlike the old suite which redirected it
into ``tests/_tmp`` and polluted the repository.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path


def make_temp_dir(prefix: str = "nordscribe_test_") -> Path:
    """Create a temporary directory owned by the current test."""
    target = Path(tempfile.gettempdir()) / f"{prefix}{uuid.uuid4().hex[:12]}"
    target.mkdir(parents=True, exist_ok=False)
    return target


def remove_temp_dir(path: Path) -> None:
    """Remove a test directory, ignoring files still locked on Windows."""
    shutil.rmtree(path, ignore_errors=True)
