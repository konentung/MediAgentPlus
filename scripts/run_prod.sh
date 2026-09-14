#!/usr/bin/env bash
# 正式模式啟動（gunicorn）
set -euo pipefail
cd "$(dirname "$0")/.."
export FLASK_ENV=production
exec gunicorn -w "${WORKERS:-2}" -k gthread --threads "${THREADS:-4}" -b "0.0.0.0:${PORT:-5000}" wsgi:app
