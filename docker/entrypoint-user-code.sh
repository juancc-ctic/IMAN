#!/usr/bin/env bash
set -euo pipefail
cd /app
export IMAN_DATABASE_URL="${IMAN_DATABASE_URL:-}"
if [[ -n "${IMAN_DATABASE_URL}" ]]; then
  alembic upgrade head
  if [[ -n "${IMAN_APPLY_SEED:-}" ]]; then
    echo "Applying seed data …"
    python - <<'EOF'
import os, psycopg2
sql = open("/app/seeds/seed.sql").read()
conn = psycopg2.connect(os.environ["IMAN_DATABASE_URL"])
conn.autocommit = True
conn.cursor().execute(sql)
conn.close()
print("Seed applied.")
EOF
    load-cordis-data
    embed-eu-projects --skip-existing &
  fi
fi
exec dagster api grpc -h 0.0.0.0 -p 4000 -m iman_ingestion.definitions
