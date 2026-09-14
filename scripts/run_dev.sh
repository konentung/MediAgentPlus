#!/usr/bin/env bash
# 開發模式啟動（自動重載）
set -euo pipefail
cd "$(dirname "$0")/.."
export FLASK_APP=wsgi.py
export FLASK_ENV="${FLASK_ENV:-development}"
exec flask run --debug --host 0.0.0.0 --port "${PORT:-5000}"
