#!/usr/bin/env bash
set -euo pipefail
cd /app
export IMAN_DATABASE_URL="${IMAN_DATABASE_URL:-}"
if [[ -n "${IMAN_DATABASE_URL}" ]]; then
  alembic upgrade head
fi
exec dagster api grpc -h 0.0.0.0 -p 4000 -m iman_ingestion.definitions
