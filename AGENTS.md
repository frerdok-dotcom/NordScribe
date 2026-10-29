# NordScribe — agent instructions

This file is the contract for any AI agent working in this repository. Read it
before changing code. The full design is in `docs/ARCHITECTURE.md`; the audit
that produced this layout is in `docs/AUDIT.md`.

## What the project is

NordScribe is a desktop application for **fully local** audio transcription
with OpenAI Whisper. Python 3.10+, CustomTkinter UI, SQLite history, PyInstaller
build. It must not send audio or transcripts to a network service. The only
network operation is downloading Whisper weights from OpenAI's public storage.

## Non-negotiable invariants

1. **Layers.** `core <- infra <- services <- ui`; `app.py` and `selftest.py`
   may import everything. `tests/test_architecture.py` fails if a service
   imports a widget or the core layer imports infrastructure.
2. **Lazy heavy imports.** `torch`, `whisper`, `sounddevice` and GUI toolkits
   must not be imported at module import time outside `ui/`. The architecture
   test checks the module level. `import nordscribe.app` must not load them.
3. **UI thread.** Tk widgets are touched only on the main thread. Worker
   callbacks go through `UiDispatcher.post(...)`; never call `after()` from a
   worker thread directly.
4. **Services do not know the UI.** They accept callbacks (progress, status,
   done, error) and run synchronously or on their own worker. The UI composes
   them.
5. **User data compatibility.**
   - settings: `~/.nordscribe/settings.json`
   - history: `~/.nordscribe/history.db`, table `transcription_history`
   - weights: `~/.cache/whisper` (Whisper reads exactly this path)
   Changing these paths requires a migration and a changelog entry.
6. **Privacy.** Do not log transcript text, audio bytes or file contents.
   Logging paths, sizes, model names and error types is fine.
7. **Tests.** No microphone, no downloaded weights, no real network. Inject
   `stream_factory` / `model_loader`; use the local HTTP server in
   `tests/test_downloader.py` for HTTP.
8. **Temporary directories.** Use `tests.make_temp_dir()`. Do not call
   `tempfile.mkdtemp()` directly: on Windows this sandbox gives such directories
   an ACL that blocks writes inside them.
9. **Do not edit** `.codegraph/codegraph.db` by hand. Reindex or sync instead.
10. **Keep the changelog current** for user-visible changes.

## Commands

PowerShell from the repository root:

```powershell
# environment (uv is preferred; pip works too)
uv venv .venv
uv pip install -e ".[dev]"

# run
.venv\Scripts\python.exe -m nordscribe
.venv\Scripts\python.exe -m nordscribe --selftest
.venv\Scripts\python.exe -m nordscribe --selftest --selftest-audio sample.wav

# quality gates (same as CI)
.venv\Scripts\ruff.exe check src tests
.venv\Scripts\ruff.exe format --check src tests
.venv\Scripts\mypy.exe
.venv\Scripts\python.exe -m pytest

# build the one-file exe (Python 3.12 is recommended)
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean packaging/NordScribe.spec

# CodeGraph
codegraph status .
codegraph sync .
codegraph explore "how does a file reach Whisper?"
```

On Linux/macOS use `.venv/bin/...` and `python3`. Tests get the package from
`pythonpath = ["src"]`, so `pytest` works without installing the project.

## Where to change what

| Task | Files |
|---|---|
| Add a Whisper model | `core/models.py` (`MODEL_SPECS`, `CACHE_ALIASES`), tests |
| Change download behaviour | `services/downloader.py`, `services/model_service.py` |
| Change cache scanning/removal | `infra/cache.py` |
| Change recording | `services/recorder.py` |
| Change transcription queue/model loading | `services/transcriber.py` |
| Add a setting | `infra/settings.py` (`Settings`), UI that reads it, tests |
| Change history schema or queries | `infra/history.py`, tests, migration note |
| Change colours/fonts | `ui/theme.py` |
| Change the window/exe icon | `src/nordscribe/assets/`, `ui/icon.py`, `packaging/NordScribe.spec` |
| Add a tab or widget | `ui/`, wire it in `ui/main_window.py` |
| Change the composition/CLI | `app.py`, `cli.py` |
| Change packaging | `packaging/NordScribe.spec`, CI workflow |
| Update architecture docs | `docs/ARCHITECTURE.md`, ADRs |

## Definition of done

- `ruff check src tests`, `ruff format --check src tests`, `mypy`, `pytest` pass.
- New behavior has a test in the relevant layer; bug fixes have a regression test.
- `CHANGELOG.md` updated for user-visible changes.
- `import nordscribe.app` remains light (no torch/whisper/ctk/sounddevice).
- No cache/build artefacts, logs, weights or `.codegraph/codegraph.db` staged.
- If files or public symbols moved, run `codegraph sync .` and update docs.

## Known trade-offs / debt

- `ui/mousewheel.py` relies on private CustomTkinter attributes
  (`_parent_canvas`, `_scrollbar`); it logs a warning when they disappear.
- The GUI has no automated end-to-end test: Tk needs a display. Pure helpers
  are tested; the rest is covered by `--selftest` and manual checks.
- PyInstaller on Python 3.14 may lag behind; use Python 3.12 for reproducible
  builds.
- Whisper model URLs and digests are static in `core/models.py`. If OpenAI
  rotates them, update the catalog and the tests.
- CodeGraph is optional for contributors; the app has no runtime dependency
  on it. The repository ships `codegraph.json` with exclusions.

## GitHub workflow

- Branch from `main`: `feat/...`, `fix/...`, `refactor/...`, `docs/...`.
- Open a pull request into `main`; CI must be green.
- Do not push directly to `main` for nontrivial changes.
- The remote is `https://github.com/frerdok-dotcom/NordScribe`.
- Use the PR template and include a test plan.

## CodeGraph

<!-- codegraph:start -->

CodeGraph is a local structural index of this repository. Use it as the first
tool for "how does X work", "where is X", "what calls X", and impact analysis.

- In an MCP-enabled agent, prefer `codegraph_explore` for almost any structural
  question. It returns verbatim source grouped by file, call paths and a blast
  radius in one call. Treat the returned source as already read; do not
  re-verify it with grep.
- On the command line use `codegraph explore "<question>"`. For one symbol use
  `codegraph node "<symbol>"`; for change analysis use
  `codegraph impact "<symbol>"`, `codegraph callers "<symbol>"`,
  `codegraph callees "<symbol>"`.
- If the answer names a file or symbol, `codegraph_explore` already includes
  its current line-numbered source. Read the file directly only when you need
  lines outside the returned range or the index reports a stale file.
- After moving files or changing public symbols, run `codegraph sync .`. A full
  rebuild is `codegraph index .`. Never edit `.codegraph/codegraph.db`.
- Do not delegate structural exploration to a file-reading subagent: it will
  read files and lose the benefit of the index. Ask CodeGraph directly.
- See `docs/CODEGRAPH.md` for setup and query recipes, and
  `docs/codegraph-system-prompt.md` for a ready-to-paste system prompt.

<!-- codegraph:end -->
