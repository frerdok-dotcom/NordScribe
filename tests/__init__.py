"""
Тесты NordScribe.

Запуск из корня проекта::

    python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

#: Каталог для временных файлов тестов: NORDSCRIBE_TEST_TMP или _tmp рядом
#: с тестами. Системный Temp не подходит — в изолированных средах он бывает
#: закрыт для создания подкаталогов.
TEST_TMP_BASE = Path(
    os.environ.get("NORDSCRIBE_TEST_TMP")
    or (Path(__file__).resolve().parent / "_tmp")
)
TEST_TMP_BASE.mkdir(parents=True, exist_ok=True)

# Файлы через mkstemp тоже складываем сюда, чтобы не мусорить в системе.
os.environ["TMPDIR"] = os.environ["TEMP"] = os.environ["TMP"] = str(TEST_TMP_BASE)
tempfile.tempdir = str(TEST_TMP_BASE)


def make_temp_dir(prefix: str = "case_") -> Path:
    """
    Создаёт временный каталог для одного теста.

    ВНИМАНИЕ: намеренно не используется :func:`tempfile.mkdtemp`. Он создаёт
    каталог с режимом 0o700, а в изолированной среде разработки запись внутрь
    такого каталога запрещена — тесты падали бы на PermissionError вместо
    проверки логики. ``Path.mkdir`` создаёт каталог с обычными правами,
    унаследованными от рабочей папки.
    """
    target = TEST_TMP_BASE / f"{prefix}{uuid.uuid4().hex[:12]}"
    target.mkdir(parents=True, exist_ok=False)
    return target


def remove_temp_dir(path: Path) -> None:
    """Удаляет каталог теста, не падая на заблокированных файлах."""
    shutil.rmtree(path, ignore_errors=True)
