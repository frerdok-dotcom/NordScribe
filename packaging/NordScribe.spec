# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the one-file NordScribe executable.

Build from the repository root::

    pyinstaller --noconfirm --clean packaging/NordScribe.spec

The result is ``dist/NordScribe.exe``. FFmpeg and Whisper weights are not
bundled: they stay in ``PATH`` and ``~/.cache/whisper`` respectively.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

project_root = Path(SPECPATH).parent
src = project_root / "src"

datas = []
binaries = []
hiddenimports = ["whisper", "scipy.special", "scipy.linalg"]

# CustomTkinter loads its theme JSON files at runtime.
datas += copy_metadata("customtkinter")
datas += collect_data_files("customtkinter")

whisper_datas, whisper_binaries, whisper_hidden = collect_all("whisper")
datas += whisper_datas
binaries += whisper_binaries
hiddenimports += whisper_hidden

a = Analysis(
    [str(src / "nordscribe" / "__main__.py")],
    pathex=[str(src)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "mypy", "ruff"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NordScribe",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
