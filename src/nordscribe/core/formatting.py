"""Small, dependency-free formatting helpers."""

from __future__ import annotations

__all__ = ["format_bytes"]


def format_bytes(num_bytes: float) -> str:
    """Convert a byte count into a human-readable string.

    Examples:
        >>> format_bytes(461234567)
        '440 МБ'
        >>> format_bytes(3_100_000_000)
        '2.9 ГБ'
    """
    size = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024 or unit == "ТБ":
            if unit in ("Б", "КБ", "МБ"):
                return f"{size:.0f} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ТБ"
