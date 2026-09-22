# Участие в разработке

Перед изменениями прочитайте [основу проекта](docs/PROJECT_BRIEF.md) и [текущую архитектуру](docs/ARCHITECTURE.md). Репозиторий содержит один продуктовый путь — игровую IDE `/studio`; не возвращайте старые демо, параллельные игровые движки, Telegram или платный API.

Работайте в отдельной ветке. Тесты, браузерные проверки и живые пробы запускайте только с временным `--data-dir`. Не коммитьте базы, журналы, авторизацию Codex и пользовательские логи.

Минимум перед коммитом:

```bash
uv run --frozen pytest -q
uv run --frozen ruff check dnd_helper tests scripts
node --check dnd_helper/static/studio.js
git diff --check
```
