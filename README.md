# PixelForge — пакетный конвертер изображений

Проект разделён на две независимые части:

- **Сервер** (`server.py`) — только FastAPI API. Не запускает клиент и не отдаёт HTML.
- **Desktop UI** (`main.py`) — лёгкий клиент на pywebview. Работает исключительно
  через HTTP API. Поднимает локальный статический сервер только для своих
  HTML/CSS/JS из `desktop_ui/`.

API заранее готов для будущего веб-клиента (CORS, загрузка файлов, SSE,
скачивание ZIP), но сам веб-клиент в рамках задачи не реализуется.

## Запуск

Терминал 1 — сервер:

```bat
python -m pip install -r requirements.txt
python server.py
```

Терминал 2 — desktop-клиент:

```bat
python main.py
```

Другой адрес API:

```bat
python main.py --api http://127.0.0.1:9000
set PIXELFORGE_API=http://127.0.0.1:9000 && python main.py
```

Сервер доступен по сети (для будущего веба):

```bat
python server.py --host 0.0.0.0 --port 8000
```

## Architecture

```
server.py             ТОЛЬКО сервер: uvicorn + FastAPI. Клиент не запускает.
desktop.py            Desktop-клиент: окно 800x600 + статика UI. Сервер не запускает.
desktop_ui/           Presentation: HTML/CSS/JS по ТЗ п.5 (800x600, тёмная тема).
api/app.py            FastAPI: REST, SSE, CORS. UI не отдаёт.
api/state.py          Оркестрация источника, задач и результатов.
api/events.py         Потокобезопасная шина SSE.
image_core.py         Service: ресайз + WebP/JPEG.
archive_validator.py  Service: ZIP/7Z/RAR, Magic Numbers.
thread_manager.py     Service: ThreadPool + FIFO + Mutex-счётчик.
config_manager.py     Data: config.json + .cache_processing.
```

Старые `ui_pyqt.py` / `ui_app.py` относятся к прежней версии и в новом запуске
не используются.

## API

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/api/health` | Проверка сервера |
| GET/PUT | `/api/config` | Настройки конвертации |
| GET | `/api/source` | Текущий источник |
| POST | `/api/source` | Локальный путь (`{"path": "..."}`) |
| POST | `/api/source/upload` | Один архив/изображение (multipart) |
| POST | `/api/source/batch` | Пакет изображений (Drag-and-Drop папки) |
| GET | `/api/job` | Задача, прогресс, журнал |
| POST | `/api/convert` | Запуск конвертации |
| POST | `/api/abort` | Остановка и очистка очереди |
| GET | `/api/events` | SSE: `log`, `state`, `source` |
| GET | `/api/output/files` | Результаты |
| GET | `/api/download` | ZIP результатов (для веба) |
| POST | `/api/output/open` | Открыть папку результатов |

Swagger: `http://127.0.0.1:8000/docs`.

## Соответствие ТЗ

- UI: окно 800×600, Single-Page, тёмная тема `#1E1E1E` / `#2D2D2D`,
  акцент `#007ACC`, ошибки `#D32F2F`, Segoe UI 10 пт / заголовки 14 пт.
- Верхняя зона — Drag-and-Drop папки/архива, кнопка «Обзор», подсказка по ТЗ.
- Средняя зона — настройки слева, журнал справа.
- Нижняя зона — прогресс, «Обработано: X из Y файлов (Z% объектов)», «Старт»/«Прервать».
- Выход: WebP (default) / JPEG, качество 10–100, Lanczos / Bicubic / Bilinear.
- Ресайз с aspect ratio и ручным W/H.
- Пул потоков = `os.cpu_count()` (2–16), FIFO `queue.Queue`, `counter++` под `threading.Lock`.
- Архивы `.zip` (+ `.7z`/`.rar`): оглавление без полной распаковки, проверка расширения
  и Magic Numbers. Чужой файл → модальное окно, красная рамка, очистка кэша.
- Кэш `.cache_processing/` в системном temp очищается после цикла/аборта.
  Конфиг — `%APPDATA%/PixelForge/config.json`.
- Валидация/конвертация замораживают настройки; прогресс на валидации —
  бесконечная анимация; по завершении — негромкий сигнал (WebAudio).
