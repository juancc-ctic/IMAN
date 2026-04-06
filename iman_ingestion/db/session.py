"""Engine and session factory from ``IMAN_DATABASE_URL``."""

from __future__ import annotations

import os
from contextlib import contextmanager
from functools import lru_cache
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def database_url() -> str:
    """Return Postgres DSN for application data."""
    url = os.environ.get("IMAN_DATABASE_URL")
    if not url:
        raise RuntimeError("IMAN_DATABASE_URL is not set")
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Shared SQLAlchemy engine (cached)."""
    return create_engine(database_url(), pool_pre_ping=True)


def get_session_factory() -> sessionmaker[Session]:
    """Session factory bound to :func:`get_engine`."""
    return sessionmaker(get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context manager yielding a Session."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
