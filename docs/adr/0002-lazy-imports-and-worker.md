# ADR 0002 — ленивые импорты и daemon-воркер

## Контекст

`main.py` импортировал `whisper_worker` → `whisper` → `torch` и
`sounddevice`. Окно появлялось после загрузки torch; `--help`/`--version`
тоже тянули тяжёлые зависимости. `ThreadPoolExecutor` использовал
non-daemon потоки: закрытие окна во время `transcribe` не завершало процесс.

## Решение

1. `core/models.py` — статический каталог моделей; `infra/cache.py`,
   `services/downloader.py`, `services/model_service.py` не импортируют
   `whisper`.
2. `Transcriber` импортирует `whisper` внутри `_load_whisper_model`.
3. `AudioRecorder` импортирует `sounddevice` внутри `start`.
4. `app.main` импортирует CustomTkinter внутри функции.
5. `Transcriber` — `queue.Queue` + один **daemon** поток; задачи
   сериализуются, закрытие окна не ждёт выполнения.

## Последствия

- `import nordscribe.app` лёгкий; тесты без torch/Whisper.
- Модель нельзя прервать во время `transcribe`; daemon-поток завершится
  вместе с процессом.
- `tests/test_architecture.py` запрещает тяжёлые импорты на уровне модуля.
