"""
Чистые функции подписей моделей.

Модуль не импортирует ни Tkinter, ни CustomTkinter, ни сетевой слой — его
можно проверять юнит-тестами без графической среды. Всё, что требует живого
виджета, живёт в :mod:`gui_components`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Optional

__all__ = [
    "MARKER_DOWNLOADED",
    "MARKER_MISSING",
    "DISPLAY_MARKERS",
    "MENU_COLOR_DOWNLOADED",
    "MENU_COLOR_MISSING",
    "clean_model_label",
    "build_model_labels",
    "label_for_model",
]

#: Цветные маркеры статуса в выпадающем списке.
MARKER_DOWNLOADED = "🟢"   # веса уже лежат в ~/.cache/whisper
MARKER_MISSING = "🔵"      # требуется загрузка (альтернатива — "🟡")

#: Любые маркеры, которые снимаются перед передачей имени в Whisper.
#: ⚪ и 🟡 оставлены для совместимости со старыми подписями.
DISPLAY_MARKERS: Sequence[str] = ("🟢", "🔵", "🟡", "⚪")

#: Цвета текста пунктов меню. Дублируют смысл маркеров: цветные эмодзи
#: рисуются не во всех сборках Tk и зависят от шрифта, а цвет текста — всегда.
MENU_COLOR_DOWNLOADED = "#4CAF50"
MENU_COLOR_MISSING = "#3B8ED0"


def clean_model_label(display_name: Optional[str]) -> str:
    """
    Снимает цветной маркер и возвращает чистое имя модели для Whisper.

    >>> clean_model_label("🟢 tiny")
    'tiny'
    >>> clean_model_label("🟡 medium (старый формат)")
    'medium'
    >>> clean_model_label(None)
    ''
    """
    name = (display_name or "").strip()
    for marker in DISPLAY_MARKERS:
        if name.startswith(marker):
            name = name[len(marker):].strip()
            break
    # Берём только первое слово: отсекает любые пояснения после имени.
    parts = name.split()
    return parts[0] if parts else ""


def build_model_labels(
    models: Iterable[str], downloaded: Iterable[str]
) -> list[str]:
    """
    Строит подписи для выпадающего списка с маркерами статуса.

    >>> build_model_labels(["tiny", "small"], {"tiny"})
    ['🟢 tiny', '🔵 small']
    """
    available = set(downloaded)
    return [
        f"{MARKER_DOWNLOADED if model in available else MARKER_MISSING} {model}"
        for model in models
    ]


def label_for_model(labels: Sequence[str], model_name: str) -> str:
    """Возвращает каноническую подпись с маркером для указанной модели."""
    for label in labels:
        if clean_model_label(label) == model_name:
            return label
    return model_name
