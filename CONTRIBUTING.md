# Contributing to NordScribe

Thanks for improving NordScribe. This document describes the development
workflow and the architectural rules that keep the codebase maintainable.

## Setup

Requirements: Python 3.10+, FFmpeg (for compressed audio), PortAudio (for the
microphone). On Windows, `sounddevice` wheels bundle PortAudio.

```powershell
uv venv .venv
uv pip install -e ".[dev]"
```

or with pip:

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
```

Run the app:

```powershell
python -m nordscribe
```

## Checks

Run these before opening a pull request; CI runs the same commands.

```powershell
ruff check src tests
ruff format --check src tests
mypy
pytest
```

The test suite must not need a microphone, downloaded Whisper weights or
internet. Fake the input stream and the model loader; use the local HTTP server
from `tests/test_downloader.py` for network tests.

## Project layout

| Path | Layer | Responsibility |
|---|---|---|
| `src/nordscribe/core/` | core | model catalog, labels, formatting, errors |
| `src/nordscribe/infra/` | infrastructure | paths, settings, logging, SQLite history, model cache |
| `src/nordscribe/services/` | services | downloader, model service, recorder, transcriber, preflight |
| `src/nordscribe/ui/` | UI | theme, dispatcher, widgets, tabs, main window |
| `src/nordscribe/app.py` | composition | logging, settings, main window, event loop |
| `src/nordscribe/selftest.py` | diagnostics | `--selftest` scenario |
| `tests/` | tests | unit and architecture tests |
| `docs/` | docs | architecture, audit, CodeGraph, ADRs |
| `packaging/` | build | PyInstaller spec |

Dependency direction: `core <- infra <- services <- ui`, with `app.py` and
`selftest.py` allowed to import everything. `tests/test_architecture.py`
enforces this.

## Coding rules

- Add type hints to public functions; `mypy --strict` is the target.
- Import `torch`, `whisper`, `sounddevice` and GUI toolkits lazily. Module
  import must stay cheap; `tests/test_architecture.py` checks this.
- Never touch Tk widgets from a worker thread. Post a callback through
  `UiDispatcher`.
- Services must not import `nordscribe.ui`.
- Put business rules in services, not in widgets. Widgets receive callbacks.
- Handle expected failures with `NordScribeError` subclasses and log the
  traceback; do not leak `urllib`/`sqlite3` errors to the UI.
- Keep user data compatible: settings in `~/.nordscribe/settings.json`, history
  in `~/.nordscribe/history.db`, weights in `~/.cache/whisper`.
- Do not log transcript text, audio content or secrets.

## Tests

- Use `tests.make_temp_dir()` for temporary directories. Do not call
  `tempfile.mkdtemp()` directly on Windows: this sandbox gives such directories
  an ACL that blocks writes.
- Use `unittest.TestCase` or pytest functions; both run under pytest.
- Add a regression test for every bug fix.
- Add a test for new behavior in the layer where it belongs. UI behavior that
  needs a real Tk loop may be covered manually; pure helpers should be tested.

## Commits and pull requests

- Branch from `main`: `feat/...`, `fix/...`, `refactor/...`, `docs/...`.
- Keep commits focused; explain the "why" in the body.
- Update `CHANGELOG.md` for user-visible changes.
- A pull request should pass CI and include a short test plan.
- Do not commit build artefacts (`dist/`, `build/`), virtual environments,
  model weights or `.codegraph/codegraph.db`.

## CodeGraph

The repository is indexed by CodeGraph (`.codegraph/`). For structural
questions, prefer `codegraph explore "..."` or the `codegraph_explore` MCP tool
over a grep/read loop. See [docs/CODEGRAPH.md](docs/CODEGRAPH.md) and the
ready-to-paste prompt in
[docs/codegraph-system-prompt.md](docs/codegraph-system-prompt.md).

Re-index after moving files or changing public symbols:

```powershell
codegraph sync .
# or a full rebuild
codegraph index .
```
