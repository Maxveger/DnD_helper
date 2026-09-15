@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Install the source environment first: uv sync --frozen
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m dnd_helper %*
