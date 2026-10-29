# CodeGraph в NordScribe

[CodeGraph](https://github.com/colbymchenry/codegraph) — локальный индекс
символов, вызовов и зависимостей для AI-агентов. В проекте уже есть
`.codegraph/` и `codegraph.json`; сам индекс в Git не попадает.

## 1. Зачем он здесь

Проект небольшой, но после рефакторинга важно быстро отвечать на вопросы:

- «как файл попадает в Whisper?»;
- «кто вызывает `ModelService.download`?»;
- «что сломается, если изменить `AudioRecorder.stop`?»;
- «где создаётся схема истории?»;
- «какие UI-модули зависят от `UiDispatcher`?».

Grep/read отвечает на это медленно и фрагментарно. `codegraph_explore`
возвращает исходники нужных символов, путь вызовов и blast radius за один
вызов.

## 2. Установка и проверка

```powershell
codegraph --version          # ожидается 1.5.x
codegraph status .           # состояние индекса
codegraph init .             # первый индекс (уже сделан)
codegraph sync .             # досинхронизировать изменения
codegraph index .            # полная перестройка
```

Индекс лежит в `.codegraph/codegraph.db` и исключён из Git. Не редактируйте
его вручную и не коммитьте.

## 3. MCP-подключение

CodeGraph устанавливает MCP-сервер командой:

```powershell
codegraph install
```

Она патчит конфигурацию агента (Claude Code, Cursor, Codex CLI, opencode,
Gemini CLI и др.). Пример ручной конфигурации:

```json
{
  "mcpServers": {
    "codegraph": {
      "command": "codegraph",
      "args": ["serve", "--mcp"]
    }
  }
}
```

По умолчанию сервер отдаёт **один** инструмент — `codegraph_explore`.
Остальные (`codegraph_node`, `codegraph_search`, `codegraph_callers`,
`codegraph_callees`, `codegraph_impact`, `codegraph_files`,
`codegraph_status`) доступны, но не показываются, чтобы агент не выбирал
неоптимальный инструмент. Включить их можно переменной:

```powershell
$env:CODEGRAPH_MCP_TOOLS = "explore,node,search,callers,callees,impact"
```

`codegraph_explore` принимает `projectPath`, поэтому можно спрашивать другой
проиндексированный проект из той же сессии.

## 4. CLI-эквиваленты

Если MCP недоступен (например, DSH без MCP-сервера), используйте CLI:

```powershell
codegraph explore "how does a file reach Whisper?"
codegraph node "AudioRecorder.stop"
codegraph query "UiDispatcher"
codegraph callers "ModelService.download"
codegraph callees "MainWindow._submit_transcription"
codegraph impact "AudioRecorder.stop" --depth 2
codegraph files
codegraph affected src/nordscribe/services/recorder.py
```

`codegraph affected` полезен перед коммитом: он показывает, какие тесты
затронуты изменёнными файлами.

## 5. Рецепты для этого репозитория

| Вопрос | Запрос |
|---|---|
| Путь файла до Whisper | `codegraph explore "How does a selected audio file reach whisper model.transcribe?"` |
| Жизненный цикл записи | `codegraph explore "AudioRecorder start stop writer thread silence trimming"` |
| Загрузка и проверка модели | `codegraph explore "ModelService download ModelCache is_valid checksum resume"` |
| Обновление UI из потока | `codegraph explore "How do worker callbacks reach Tk through UiDispatcher?"` |
| История | `codegraph explore "TranscriptionHistory schema add all clear"` |
| Влияние изменения `stop` | `codegraph impact "AudioRecorder.stop" --depth 2` |
| Кто создаёт окно | `codegraph callers "build_window"` |
| Слои | `codegraph explore "core infra services ui dependency rules"` |

## 6. Правила работы

1. **Сначала CodeGraph, потом grep.** Для структурного вопроса один
   `codegraph_explore` дешевле десятка чтений файлов.
2. **Доверяйте исходнику из ответа.** Он уже прочитан; не перепроверяйте
   grep-ом. Если нужен файл целиком или строки вне диапазона — читайте файл.
3. **Следите за stale banner.** После правок CodeGraph может показать, что
   файл ещё не синхронизирован; прочитайте его напрямую.
4. **Не делегируйте exploration file-reading субагенту.** Он будет читать
   файлы и потеряет смысл индекса.
5. **После переезда файлов** — `codegraph sync .`; после крупного
   рефакторинга — `codegraph index .`.
6. **Не редактируйте `.codegraph/`** и не коммитьте базу.

## 7. Исключения

`codegraph.json` исключает `dist/`, `build/`, `venv/`, `_pip_tmp/`,
`_uv_cache/`, `torchinductor_fr0oz/`, `tests/_tmp/`, `tests/pytest-cache-files-*`,
`docs/images/`. CodeGraph также уважает `.gitignore`. Если нужно
проиндексировать намеренно игнорируемый исходник, используйте `include` в
`codegraph.json`, а не отключайте `.gitignore`.

## 8. Troubleshooting

| Симптом | Что делать |
|---|---|
| `status` показывает устаревший индекс | `codegraph sync .` |
| «stale lock» | `codegraph unlock .` |
| демон мешает | `codegraph daemon`, выбрать и остановить; или `CODEGRAPH_NO_DAEMON=1` |
| лишний шум в индексе | проверить `codegraph.json` и `.gitignore` |
| не нужна телеметрия | `codegraph telemetry off` или `CODEGRAPH_TELEMETRY=0` |
| MCP-инструмент не виден | перезапустить агент после `codegraph install` |

## 9. Готовый системный промпт

Файл: [codegraph-system-prompt.md](codegraph-system-prompt.md). Скопируйте
блок в системную инструкцию агента или используйте короткую версию из
`AGENTS.md` (секция CodeGraph).
