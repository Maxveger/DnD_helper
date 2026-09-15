# Сторонние компоненты

Программа использует Python, FastAPI/Starlette, Uvicorn, HTTPX, Pydantic, SQLite и PyInstaller. Точные версии и источники пакетов записаны в `uv.lock`. Скрипт сборки копирует доступные файлы LICENSE/COPYING/NOTICE из метаданных зависимостей в папку `licenses` готового пакета.

Для конвертации голосовых используется бинарный FFmpeg из пакета `imageio-ffmpeg`. PyInstaller собирает его вместе с приложением через `--collect-all imageio_ffmpeg`, включая поставленные пакетом файлы лицензирования. `imageio-ffmpeg` имеет BSD-лицензию; у включённого FFmpeg собственные условия, зависящие от сборки.

Скрипт сохраняет вывод `ffmpeg -version` и `ffmpeg -L` в `licenses/FFMPEG-VERSION.txt` и `licenses/FFMPEG-LICENSE.txt`. Они описывают именно включённый в пакет бинарный файл. Исходники и инструкции получения сборок FFmpeg перечислены в upstream-проекте `imageio-ffmpeg`. Лицензия нашего исходного проекта ещё не выбрана; опубликованного выпуска пока нет.

Источники компонентов:

- https://github.com/imageio/imageio-ffmpeg
- https://ffmpeg.org/
- https://pyinstaller.org/
- https://www.python.org/
- https://fastapi.tiangolo.com/
- https://www.starlette.io/
- https://www.uvicorn.org/
- https://www.python-httpx.org/
- https://docs.pydantic.dev/
