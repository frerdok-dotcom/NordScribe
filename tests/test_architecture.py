"""Architecture guardrails: layer dependencies and lazy heavy imports.

These tests are intentionally static (AST only). They catch the regressions
that are easy to introduce in a desktop app: a service importing a widget, the
core layer pulling in torch, or a GUI toolkit leaking into a backend module.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from setuptools import find_packages

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = SRC / "nordscribe"

#: A layer may import itself and the layers listed here.
LAYER_RULES: dict[str, set[str]] = {
    "core": {"core"},
    "infra": {"core", "infra"},
    "services": {"core", "infra", "services"},
    "ui": {"core", "infra", "services", "ui"},
}

#: Importing these at module level would defeat lazy startup and test speed.
HEAVY_MODULES = {"torch", "whisper", "sounddevice"}

#: Only the UI and the entry points may import a GUI toolkit at module level.
GUI_MODULES = {"customtkinter", "tkinter"}


def _layer(path: Path) -> str | None:
    """Return the top-level layer for ``path`` inside the package."""
    try:
        relative = path.relative_to(PACKAGE)
    except ValueError:
        return None
    if len(relative.parts) < 2:
        return None
    return relative.parts[0]


def _imported_layers(tree: ast.Module) -> set[str]:
    """Layers imported by absolute ``nordscribe.*`` imports."""
    found: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            parts = name.split(".")
            if parts[0] == "nordscribe" and len(parts) >= 2:
                found.add(parts[1])
    return found


def _module_level_imports(tree: ast.Module) -> set[str]:
    """Top-level import roots, including imports inside module-level ``if``."""
    roots: set[str] = set()
    stack: list[ast.stmt] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.If):
            stack.extend(node.body)
            stack.extend(node.orelse)
    return roots


class LayerRuleTest(unittest.TestCase):
    """The dependency direction is core <- infra <- services <- ui."""

    def test_no_forbidden_layer_imports(self) -> None:
        offenders: list[str] = []
        for path in PACKAGE.rglob("*.py"):
            layer = _layer(path)
            if layer not in LAYER_RULES:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            allowed = LAYER_RULES[layer]
            for imported in _imported_layers(tree):
                if imported not in allowed:
                    offenders.append(f"{path.relative_to(SRC)}: {layer} -> {imported}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_setuptools_finds_every_package(self) -> None:
        packages = set(find_packages(str(SRC)))
        self.assertEqual(
            packages,
            {
                "nordscribe",
                "nordscribe.core",
                "nordscribe.infra",
                "nordscribe.services",
                "nordscribe.ui",
            },
        )


class LazyImportTest(unittest.TestCase):
    """Heavy and GUI imports stay out of module import time."""

    def test_no_heavy_imports_at_module_level(self) -> None:
        offenders: list[str] = []
        for path in PACKAGE.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            roots = _module_level_imports(tree)
            for root in roots & HEAVY_MODULES:
                offenders.append(f"{path.relative_to(SRC)}: imports {root}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_gui_imports_only_in_ui(self) -> None:
        offenders: list[str] = []
        for path in PACKAGE.rglob("*.py"):
            layer = _layer(path)
            if layer is None or layer == "ui":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            roots = _module_level_imports(tree)
            for root in roots & GUI_MODULES:
                offenders.append(f"{path.relative_to(SRC)}: imports {root}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_package_does_not_import_itself_eagerly(self) -> None:
        """``import nordscribe`` must stay trivial (no app/UI side effects)."""
        path = PACKAGE / "__init__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        self.assertEqual(_imported_layers(tree), set())
