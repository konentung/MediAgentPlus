#!/usr/bin/env bash
# 本機建立 PostgreSQL 使用者與資料庫（macOS Homebrew / Linux 皆可用）
# 用法：./scripts/setup_db.sh [db_name] [db_user] [db_password]
set -euo pipefail

DB_NAME="${1:-mediagent}"
DB_USER="${2:-mediagent}"
DB_PASS="${3:-mediagent}"

if ! command -v psql >/dev/null 2>&1; then
  echo "找不到 psql，請先安裝 PostgreSQL："
  echo "  macOS : brew install postgresql@16 && brew services start postgresql@16"
  echo "  Ubuntu: sudo apt install postgresql && sudo systemctl start postgresql"
  exit 1
fi

# macOS Homebrew 預設以目前使用者連線；Linux 通常需要 sudo -u postgres
if psql -d postgres -c "select 1" >/dev/null 2>&1; then
  PSQL="psql -d postgres -v ON_ERROR_STOP=1"
else
  PSQL="sudo -u postgres psql -d postgres -v ON_ERROR_STOP=1"
fi

$PSQL <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASS}';
  END IF;
END
\$\$;
SQL

if ! $PSQL -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  $PSQL -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
fi

echo "完成：postgresql+psycopg://${DB_USER}:${DB_PASS}@localhost:5432/${DB_NAME}"
