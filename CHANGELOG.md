# Changelog

All notable changes to NordScribe are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Application logo in `src/nordscribe/assets`: window/taskbar icon and the
  multi-size `.ico` embedded in the Windows executable. The wordmark was
  removed and the emblem enlarged so it reads better at small icon sizes.
- `packaging/Create-Shortcut.ps1` for a desktop shortcut that uses the EXE icon.

## [1.1.0] — 2026-10-29

### Added

- Layered `src/nordscribe` package: `core`, `infra`, `services`, `ui`.
- Architecture tests that enforce layer dependencies and lazy heavy imports.
- `UiDispatcher`: all worker callbacks reach Tk through one queue on the UI thread.
- `CopyButton` widget shared by the transcription and history tabs.
- `ScrollController` for predictable mouse-wheel scrolling, extracted from the history tab.
- `ModelService` as the single entry point for model catalog, cache, download and delete.
- `ModelCache`, `downloader`, `recorder`, `transcriber` and `preflight` services with injected dependencies.
- `--version` CLI flag and `python -m nordscribe` entry point.
- Structured docs: `ARCHITECTURE.md`, `AUDIT.md`, `CODEGRAPH.md`, ADRs, `AGENTS.md`.
- `CONTRIBUTING.md`, `CHANGELOG.md`, `LICENSE`, issue/PR templates, Dependabot.
- Pre-commit configuration, EditorConfig, VS Code tasks and recommended extensions.
- `codegraph.json` exclusions and a CodeGraph system prompt.

### Changed

- The project is installed as a package: `pip install -e ".[dev]"` and
  `python -m nordscribe` (or the `nordscribe` script). `python main.py` is no
  longer the entry point.
- `requirements.txt` was removed; `pyproject.toml` is the single dependency source.
- `main.spec` moved to `packaging/NordScribe.spec` and points at the package entry point.
- CI installs the package once and runs a strict `ruff format --check`.
- Settings are validated field by field and saved atomically.
- The redundant history index was removed; `ORDER BY id DESC` already uses the
  primary-key index.
- Error messages are wrapped in `NordScribeError` subclasses instead of leaking
  `urllib`, `sqlite3` or `torch` exceptions.
- The transcription worker uses one daemon thread instead of a non-daemon
  `ThreadPoolExecutor`, so closing the window no longer waits for a long job.
- The recorder accepts the first PortAudio callback, makes `stop()` idempotent
  and passes ownership of the WAV path to the caller.

### Removed

- Dead `update_progressbar` / `set_progress` chain (the progress bar is indeterminate).
- Unused `trim_silence_bounds` (the recorder trims silence while streaming).
- Unused `ollama` dependency.
- Duplicated clipboard and model-deletion code.
- Absolute Python interpreter path from the committed VS Code settings.

### Fixed

- `pip install` now includes `theme`, `tab_*` and the other UI modules (the old
  `py-modules` list was incomplete).
- UI updates from worker threads no longer call `after()` directly.
- Pytest no longer pollutes `tests/_tmp`; temporary files use the system temp
  directory with permissions that work in the Windows sandbox.
- The history placeholder is not duplicated, and `load()` is idempotent.
- Preflight `fatal` is used: an unwritable model cache produces a red status.

## [1.0.0]

Initial public version: Whisper transcription, microphone recording, model
manager with resume and checksum verification, history in SQLite, environment
checks and a PyInstaller build.
