# Аудит NordScribe

Дата: 2026-10-29. Объём: весь Python-код, тесты, упаковка, CI, документация,
Git-гигиена. Цель — не косметика, а привести проект в состояние, в котором его
можно безопасно развивать: понятные слои, отсутствие мёртвого кода,
предсказуемые потоки, проверяемые границы.

## 1. Исходное состояние (baseline)

| Проверка | Результат |
|---|---|
| `pytest` (старый набор) | все тесты проходили |
| `ruff check .` | без замечаний |
| `ruff format --check .` | падал на недоступных временных каталогах |
| `mypy .` | 3 ошибки (`gui_components.py`, `tab_history.py`) |
| Установка пакета | `pyproject.toml` не включал `theme`, `tab_*` — установленная копия была бы нерабочей |
| Git | ветка `main`, рабочее дерево чистое; `local-ai-transcription-4b65b` уже влита |

## 2. Находки

Severity: **critical** — ломает поставку, **high** — ломает поведение,
**medium** — поддерживаемость/надёжность, **low** — гигиена.

### A01. Неполный `py-modules` — critical

`pyproject.toml` перечислял `main`, `download_manager`, `whisper_worker`,
`gui_components`, `model_labels`, `settings`, `preflight`, `history`, но не
`theme`, `tab_history`, `tab_transcription`. `pip install` ставил бы пакет,
который падает на импорте. Плюс сам список модулей — ручной, его легко забыть.

**Решение:** `src/nordscribe` и `[tool.setuptools.packages.find]`; тест
`test_architecture.py` проверяет слои, а `pip install -e .` — весь пакет.

### A02. Плоская раскладка и неявные зависимости — high

11 модулей в корне, `main.py` на 664 строки, `tab_history.py` на 621 строку.
Тесты импортировали `import history`, `import tab_history`, то есть зависели от
текущего каталога, а не от пакета. `gui_components` смешивал шрифты, меню,
окно диспетчера и сеть.

**Решение:** пакет `core` / `infra` / `services` / `ui`, `main_window.py`,
`history_tab.py`, `model_manager.py`, `widgets.py`, `mousewheel.py`,
`dispatcher.py`. Правила зависимостей закреплены тестом.

### A03. Тяжёлые импорты на старте — high

`main.py` импортировал `whisper_worker` → `whisper` → `torch` и
`sounddevice`; `preflight` импортировал `download_manager` → `whisper`.
`--selftest`/`--help` тоже тянули torch. На слабых машинах окно появлялось
через секунды, тесты требовали torch без необходимости.

**Решение:** каталог моделей статический (`core/models.py`), `transcriber`
импортирует `whisper` внутри загрузчика, `recorder` импортирует
`sounddevice` внутри `start`, `app` импортирует CustomTkinter внутри `main`.
Проверено: `import nordscribe.app` не загружает torch/whisper/ctk/sounddevice.

### A04. Закрытие окна могло ждать многоминутный `transcribe` — high

`WhisperWorker` использовал `ThreadPoolExecutor`; его потоки не daemon.
`shutdown(wait=False)` не прерывает уже выполняющуюся задачу, а интерпретатор
ждёт non-daemon потоки при выходе. Пользователь закрывал окно — процесс висел.

**Решение:** `Transcriber` — очередь + один daemon-воркер. Очередь
отменяется, текущая задача не прерывается (torch не даёт безопасного API), но
процесс не ждёт её.

### A05. Обновление Tk из рабочих потоков — high

`AudioRecorder.on_tick` вызывался из writer-потока, а `MainWindow` делал
`self.update_status(...)` → `self.after(...)`. Вызов `after` из чужого потока
не гарантирован. То же было в диспетчере моделей.

**Решение:** `UiDispatcher` — `SimpleQueue` + `after(33ms)` на главном потоке.
Все worker-callback-и только кладут задачу в очередь. `ModelManagerWindow`
получает `post` от главного окна.

### A06. Дублирование clipboard-логики — medium

`TranscriptionTab.copy_transcript` и `HistoryTab.copy_text` — почти одинаковые
40 строк: буфер, `TclError`, таймер 1.5 с, отмена предыдущего таймера.

**Решение:** `ui/widgets.py::CopyButton`; обе вкладки используют его.

### A07. Дублирование удаления моделей — medium

`ModelManagerWindow._delete_model` заново перебирал `path` и `.part`, считал
размер и обрабатывал `OSError`, хотя `download_manager.remove_model_file` уже
умел это. UI обходил сервис и обращался к файлам напрямую.

**Решение:** `ModelService.delete()` → `ModelCache.remove()`; UI получает
`DeleteResult` с именами, размером и флагом «модель всё ещё доступна».

### A08. Мёртвый код — medium

| Код | Почему мёртвый | Действие |
|---|---|---|
| `MainWindow.update_progressbar` | ни одного вызова | удалён |
| `TranscriptionTab.set_progress` | вызывался только из `update_progressbar` | удалён |
| `whisper_worker.trim_silence_bounds` | использовался только тестами; рекордер режет тишину потоково | удалён вместе с тестами |
| `requirements.txt` | дублировал `pyproject`, содержал неиспользуемый `ollama` | удалён |
| `gui_components._delete_model` | дубль `remove_model_file` | заменён сервисом |

`history.count()` оставлен: это дешёвый диагностический метод репозитория,
он используется тестами. `preflight.CheckResult.fatal` теперь используется
(красный статус при недоступном кэше), а не висит мёртвым полем.

### A09. Расхождение зависимостей — medium

`requirements.txt`: `ollama` (в коде нет), `pyproject` без `ollama`; версии
могли разъехаться. CI ставил `requirements.txt` и `.[dev]` — два прохода,
два источника истины.

**Решение:** единственный источник — `pyproject.toml`; CI ставит
`pip install -e ".[dev]"` один раз. `requirements.txt` удалён, README обновлён.

### A10. Настройки без валидации и неатомарная запись — medium

`Settings.load` делал `cls(**{...})`: `"model": 5` или `"trim_silence": "no"`
попадали в dataclass, ломая UI/логику. `save` писал прямо в файл — падение
могло оставить обрезанный JSON.

**Решение:** валидация по типу каждого поля (неверное — дефолт, остальные
поля сохраняются), атомарная запись `*.tmp` + `Path.replace`, тесты.

### A11. Лишний индекс истории — medium

`CREATE INDEX ... ON transcription_history (id DESC)` дублирует индекс
первичного ключа (`INTEGER PRIMARY KEY` — rowid). Плюс `ORDER BY id DESC`
и так идёт по rowid.

**Решение:** индекс удалён из схемы; тест схемы обновлён. Существующие базы
не ломаются: `CREATE TABLE IF NOT EXISTS` просто не создаёт новый индекс,
старый остаётся безвредным.

### A12. Рекордер: первый блок и двойной `stop` — medium

- `AudioRecorder.start` вызывал `stream.start()` до `self._stream = stream`;
  если PortAudio отдавал первый callback сразу, блок терялся.
- `stop()` не очищал `_temp_path`; повторный вызов мог вернуть тот же путь,
  то есть передать владение файлом дважды.

**Решение:** `_stream` выставляется до `start()`; при ошибке — сбрасывается.
`stop()` идемпотентен, забирает путь и очищает внутреннее поле; добавлен тест
на первый callback и повторный `stop`.

### A13. Приватный `whisper._MODELS` — medium

`download_manager.get_model_url` читал `whisper._MODELS`; это приватный
словарь, который может исчезнуть, и он тянет torch в любой импорт.

**Решение:** статический `ModelSpec` с URL/SHA-256/именем файла; тест
проверяет, что SHA есть в URL, а имя файла совпадает. `large` →
`large-v3.pt` закреплено тестом.

### A14. Глобальный `unbind_all` колеса — medium

`tab_history` снимал **все** глобальные привязки `<MouseWheel>` на toplevel,
включая чужие обработчики, и ставил свои. Это работало, пока список один, но
хрупко.

**Решение:** логика вынесена в `ScrollController`, поведение сохранено,
привязки снимаются только при `detach()`. Остаточный риск: приватные
атрибуты `_parent_canvas`, `_scrollbar` CustomTkinter. Это записано в
`AGENTS.md` и `ARCHITECTURE.md`; при обновлении CTk нужно проверить.

### A15. `fatal` не использовался — low

`preflight` вычислял `fatal`, но `summarize` его игнорировал, и статус всегда
был оранжевым. Недоступный кэш моделей — критическая проблема.

**Решение:** `has_fatal()` + красный цвет статуса для fatal-проверок.

### A16. Цвета-литералы в UI — low

В `gui_components` и вкладках встречались `"green"`, `"orange"`, `"gray"`
рядом с константами `theme`. Часть статусов не следовала палитре.

**Решение:** `STATUS_*` и `COLOR_*` из `ui/theme.py`; литералы заменены.

### A17. Git/IDE-гигиена — low

- `.vscode/settings.json` содержал абсолютный путь к Python конкретного
  пользователя.
- `.gitignore` не покрывал `venv/`, `.venv/`, `.pytest_cache`, `.mypy_cache`,
  `.ruff_cache`, `_uv_cache`, `torchinductor_*`, `tests/_tmp` (последний был,
  но не всё остальное), `*.log`, IDE-мусор.
- В истории был закоммичен `.pyc` (`a85fc35`); `.gitignore` добавили позже.

**Решение:** переносимые `.vscode/settings.json`, `extensions.json`,
`tasks.json`; расширенный `.gitignore`; `.gitattributes`; проверка
`git ls-files '*.pyc'` (см. раздел 4).

### A18. Нет инженерной обвязки — low

Не было `LICENSE`, `CONTRIBUTING`, `CHANGELOG`, `AGENTS.md`, шаблонов
issue/PR, Dependabot, pre-commit, EditorConfig; CI не проверял формат строго.

**Решение:** добавлены. CI: `ruff check`, `ruff format --check`, `mypy`,
`pytest`; ручная сборка Windows — отдельный workflow.

### A19. `--selftest` и `main` смешаны — low

`main.py` содержал `_selftest_audio_path`, `_selftest_model`, `_run_selftest`.
Точка входа раздувалась диагностикой.

**Решение:** `selftest.py`, `cli.py`, `app.py`; `--version` не грузит GUI.

### A20. Тестовый мусор и ACL — low

`tests/__init__.py` перенаправлял `TEMP` в `tests/_tmp`; `tempfile.mkdtemp`
на Windows-песочнице создаёт каталог с ACL, запрещающим запись. В репозитории
накопились десятки `tests/_tmp/*` и `pytest-cache-files-*`.

**Решение:** `tests.make_temp_dir()` использует системный temp и
`Path.mkdir` (без `mkdtemp`); `tests/_tmp`, `pytest-cache-files-*` исключены
из pytest/ruff/mypy и `.gitignore`. Старые каталоги с deny-ACL не удаляются
этим процессом; они локальны и не попадают в Git.

## 3. Что оставлено сознательно

| Решение | Причина |
|---|---|
| SQLite-соединение на операцию | `sqlite3` не потокобезопасен по умолчанию; короткое соединение не держит файл на Windows |
| Статический каталог моделей | убирает приватный API Whisper и torch из импорта; URL/хеши стабильны |
| Один глобальный обработчик колеса | обходит конфликт CTkScrollableFrame; несколько списков не поддерживаются, но их и нет |
| `Text`-поля карточек заблокированы | нельзя стереть текст, но можно выделять и копировать |
| Модель не прерывается при закрытии | torch не даёт безопасного прерывания; daemon-поток решает проблему зависания |
| `history.count()` | дешёвый диагностический API, используется тестами |
| Нет GUI e2e | Tk требует дисплей; `--selftest` + ручные проверки |

## 4. Проверка после рефакторинга

```powershell
ruff check src tests          # All checks passed
ruff format --check src tests # all files already formatted
mypy                          # Success
pytest                        # 139 passed
python -m nordscribe --version
python -m nordscribe --selftest
```

Дополнительно:

- `import nordscribe.app` не содержит `torch`, `whisper`, `customtkinter`,
  `sounddevice` в `sys.modules`;
- `pip install`/`PyInstaller` проверяются в CI и вручную (см. README);
- `PyInstaller` собрал `dist/NordScribe.exe` (248 МБ). Запуск one-file exe в
  этой песочнице не доведён до конца: распаковка в `%TEMP%` блокируется ACL
  (`MSVCP140_ATOMIC_WAIT.dll: Permission denied`), это ограничение среды, а не
  приложения; сборка завершилась успешно;
- архитектурные тесты падают при импорте `ui` из `services` и при тяжёлом
  импорте на уровне модуля.

## 5. Остаточные риски

1. **CustomTkinter private API.** `ScrollController` и `model_menu` зависят от
   `_parent_canvas`, `_scrollbar`, `_dropdown_menu`. При мажорном обновлении
   CTk колесо/цвета могут тихо деградировать; в коде есть warning в журнал.
2. **PyInstaller и Python 3.14.** Сборка рекомендуется на 3.12; CI-матрица —
   3.12/3.13. Для 3.14 может понадобиться обновление PyInstaller.
3. **Нет автоматического GUI-теста.** `--selftest` проверяет окно и
   окружение, но не кликает по сценарию. Регрессии вкладок ловятся вручную.
4. **Модели не прерываются.** Закрытие окна не убивает текущий `transcribe`;
   daemon-поток завершится вместе с процессом, но GPU/CPU может быть занят до
   выхода.
5. **Статические URL моделей.** Если OpenAI поменяет адреса/хеши, каталог
   нужно обновить; тест `test_every_spec_has_https_url_and_sha` не поймает
   сетевой 404, только структуру.
6. **`tests/_tmp` и `pytest-cache-files-*`** могут остаться на машине
   разработчика с deny-ACL; они исключены из инструментов и Git.
