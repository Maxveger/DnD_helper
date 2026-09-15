#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo 'Сначала выполните: uv sync --frozen'
    exit 1
fi
exec .venv/bin/python -m dnd_helper "$@"
