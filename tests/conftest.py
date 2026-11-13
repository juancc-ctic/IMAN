"""Shared fixtures: a real Postgres for Tender Lifecycle tests.

Database tests run only when ``IMAN_TEST_DATABASE_URL`` is set; they are
skipped otherwise. The schema is rebuilt from Alembic migrations once per
session and every table is truncated between tests.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def _migrated_engine():
    url = os.environ.get("IMAN_TEST_DATABASE_URL")
    if not url:
        pytest.skip("IMAN_TEST_DATABASE_URL not set; skipping database tests")

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    previous = os.environ.get("IMAN_DATABASE_URL")
    os.environ["IMAN_DATABASE_URL"] = url
    try:
        cfg = Config(str(_REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
        command.upgrade(cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("IMAN_DATABASE_URL", None)
        else:
            os.environ["IMAN_DATABASE_URL"] = previous
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(_migrated_engine) -> Iterator["Session"]:
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with _migrated_engine.begin() as conn:
        tables = conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename <> 'alembic_version'"
            )
        ).scalars().all()
        if tables:
            conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE"))

    session = Session(_migrated_engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.rollback()
        session.close()
