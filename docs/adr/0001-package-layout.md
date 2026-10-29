# ADR 0001 — пакетная архитектура

## Контекст

Код лежал в корне репозитория: 11 модулей, `main.py` на 664 строки,
`tab_history.py` на 621 строку. `pyproject.toml` перечислял модули вручную и
забывал `theme`, `tab_history`, `tab_transcription`. Тесты импортировали
`import history` и зависели от рабочего каталога.

## Решение

Перейти на `src/`-layout и пакет `nordscribe` с четырьмя слоями:

```text
core <- infra <- services <- ui
```

`app.py` и `selftest.py` — composition root. Слои проверяются AST-тестом
`tests/test_architecture.py`. Публичная точка входа — `nordscribe.app:main`,
запуск — `python -m nordscribe` или скрипт `nordscribe`.

## Последствия

- `pip install -e .` ставит все модули; `py-modules` больше не нужен.
- Тесты не зависят от CWD, `pythonpath = ["src"]` в pytest.
- Появилась цена: старый `python main.py` больше не работает; это записано в
  CHANGELOG и README.
- Добавление нового модуля требует осознанного выбора слоя.
