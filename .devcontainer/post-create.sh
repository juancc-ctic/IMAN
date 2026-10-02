#!/usr/bin/env bash
set -euo pipefail

pip install --upgrade pip
pip install -e '.[dev]'

# Private Dagster instance (Postgres-backed, same config as the Docker stack). Kept out of
# docker/dagster_home, whose logs/storage are root-owned by the production containers.
mkdir -p "${DAGSTER_HOME}"
cp docker/dagster_home/dagster.yaml "${DAGSTER_HOME}/dagster.yaml"

alembic upgrade head
