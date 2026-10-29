"""Whisper model catalog, cache aliases and display labels.

This module is deliberately pure: it does not import ``whisper``, ``torch`` or
any GUI toolkit. The catalog is static so the UI starts quickly and tests do
not need multi-gigabyte dependencies.

The URL format is the one used by ``whisper._download()``: the SHA-256 digest
is the second-to-last path segment and the file name is the last one. Keeping
both in :class:`ModelSpec` makes the network layer independent from the private
``whisper._MODELS`` dictionary.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlsplit

__all__ = [
    "CACHE_ALIASES",
    "DEFAULT_MODEL_NAME",
    "DISPLAY_MARKERS",
    "MARKER_DOWNLOADED",
    "MARKER_MISSING",
    "MENU_COLOR_DOWNLOADED",
    "MENU_COLOR_MISSING",
    "MODEL_NAMES",
    "MODEL_SPECS",
    "MODEL_SPECS_BY_NAME",
    "ModelSpec",
    "aliases_for_filename",
    "build_model_labels",
    "clean_model_label",
    "label_for_model",
]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BASE_URL = "https://openaipublic.azureedge.net/main/whisper/models/"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Everything the application needs to know about one Whisper model."""

    name: str
    filename: str
    url: str
    sha256: str
    size_label: str

    def __post_init__(self) -> None:
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError(f"{self.name}: invalid SHA-256 {self.sha256!r}")
        if self.sha256 not in self.url:
            raise ValueError(f"{self.name}: SHA-256 is missing from the URL")
        if urlsplit(self.url).path.rsplit("/", 1)[-1] != self.filename:
            raise ValueError(f"{self.name}: URL file name and filename differ")


#: Models shown in the interface, in menu order.
MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec(
        name="tiny",
        filename="tiny.pt",
        url=f"{_BASE_URL}65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9/tiny.pt",
        sha256="65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9",
        size_label="~75 МБ",
    ),
    ModelSpec(
        name="base",
        filename="base.pt",
        url=f"{_BASE_URL}ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e/base.pt",
        sha256="ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e",
        size_label="~140 МБ",
    ),
    ModelSpec(
        name="small",
        filename="small.pt",
        url=f"{_BASE_URL}9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794/small.pt",
        sha256="9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794",
        size_label="~460 МБ",
    ),
    ModelSpec(
        name="medium",
        filename="medium.pt",
        url=f"{_BASE_URL}345ae4da62f9b3d59415adc60127b97c714f32e89e936602e85993674d08dcb1/medium.pt",
        sha256="345ae4da62f9b3d59415adc60127b97c714f32e89e936602e85993674d08dcb1",
        size_label="~1.5 ГБ",
    ),
    ModelSpec(
        # whisper.load_model("large") resolves to these weights; the file must
        # be called large-v3.pt or the library downloads it again.
        name="large",
        filename="large-v3.pt",
        url=f"{_BASE_URL}e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb/large-v3.pt",
        sha256="e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb",
        size_label="~2.9 ГБ",
    ),
)

MODEL_NAMES: tuple[str, ...] = tuple(spec.name for spec in MODEL_SPECS)

MODEL_SPECS_BY_NAME = MappingProxyType({spec.name: spec for spec in MODEL_SPECS})

DEFAULT_MODEL_NAME = "base"

#: Official Whisper cache file names and the model names they satisfy.
#: ``large-v3.pt`` is what ``load_model("large")`` actually reads, so it also
#: advertises the generic ``large`` name.
CACHE_ALIASES: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
    {
        "tiny.pt": ("tiny",),
        "tiny.en.pt": ("tiny.en",),
        "base.pt": ("base",),
        "base.en.pt": ("base.en",),
        "small.pt": ("small",),
        "small.en.pt": ("small.en",),
        "medium.pt": ("medium",),
        "medium.en.pt": ("medium.en",),
        "large-v1.pt": ("large-v1", "large"),
        "large-v2.pt": ("large-v2", "large"),
        "large-v3.pt": ("large-v3", "large"),
        "large.pt": ("large",),
        "turbo.pt": ("turbo",),
    }
)

#: Coloured status markers in the model menu.
MARKER_DOWNLOADED = "🟢"
MARKER_MISSING = "🔵"

#: Markers accepted by :func:`clean_model_label`, including legacy ones.
DISPLAY_MARKERS: Sequence[str] = ("🟢", "🔵", "🟡", "⚪")

#: Text colours for menu items. Emoji rendering depends on the installed font,
#: so the status is duplicated by the text colour.
MENU_COLOR_DOWNLOADED = "#4CAF50"
MENU_COLOR_MISSING = "#3B8ED0"


def aliases_for_filename(filename: str) -> tuple[str, ...]:
    """Return model names that a cache file satisfies."""
    return CACHE_ALIASES.get(filename, ())


def clean_model_label(display_name: str | None) -> str:
    """Remove a status marker and trailing explanation from a menu label.

    Examples:
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
            name = name[len(marker) :].strip()
            break
    parts = name.split()
    return parts[0] if parts else ""


def build_model_labels(models: Iterable[str], downloaded: Iterable[str]) -> list[str]:
    """Build menu labels with a green or blue status marker.

    Examples:
        >>> build_model_labels(["tiny", "small"], {"tiny"})
        ['🟢 tiny', '🔵 small']
    """
    available = set(downloaded)
    return [
        f"{MARKER_DOWNLOADED if model in available else MARKER_MISSING} {model}" for model in models
    ]


def label_for_model(labels: Sequence[str], model_name: str) -> str:
    """Return the canonical label for ``model_name``.

    Falls back to the bare model name when the label list is out of date.
    """
    for label in labels:
        if clean_model_label(label) == model_name:
            return label
    return model_name
