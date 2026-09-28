#!/usr/bin/env bash
# 正式模式啟動（gunicorn）
set -euo pipefail
cd "$(dirname "$0")/.."
export FLASK_ENV=production
# --timeout 拉長到 120 秒：處方箋辨識與 AI 對話一次可能 10–30 秒，預設 30 秒會被 gunicorn 砍掉
exec gunicorn -w "${WORKERS:-2}" -k gthread --threads "${THREADS:-4}" --timeout "${TIMEOUT:-120}" -b "0.0.0.0:${PORT:-5000}" wsgi:app
