"""SQLAlchemy engine + session management.

Usage:
    from db.connection import get_engine, get_session, init_db

    engine = get_engine()          # create_engine(DATABASE_URL)
    init_db(engine)                # create_all()
    with get_session() as session: # sessionmaker(bind=engine)
        ...
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from config import get_database_url as _config_database_url


def get_database_url() -> str:
    # Single source of truth: config loads .env and exposes DATABASE_URL.
    return _config_database_url()


def get_engine(url: str | None = None):
    if url is None:
        url = get_database_url()

    kwargs: dict = {"echo": os.getenv("SQL_ECHO", "0") == "1"}

    if url.startswith("sqlite"):
        from sqlalchemy.pool import StaticPool
        kwargs.update({
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        })
    else:
        kwargs.update({
            "pool_size": 5,
            "max_overflow": 10,
            "pool_pre_ping": True,
        })

    return create_engine(url, **kwargs)


def get_session_factory(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def get_session(engine) -> Session:
    if engine is None:
        raise RuntimeError("Database not available")
    factory = get_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(engine) -> None:
    """Create all tables defined in models.py (idempotent)."""
    from db.models import Base
    Base.metadata.create_all(bind=engine)


def get_db_engine(request) -> "Engine":
    """Safely get the DB engine from request.app.state.

    Returns the engine or raises HTTPException 503 if not available.
    """
    engine = getattr(request.app.state, "db_engine", None)
    if engine is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail="Database not available. Check DATABASE_URL configuration.",
        )
    return engine


def enable_uuid_extension(engine) -> None:
    """Enable pgcrypto for gen_random_uuid() (idempotent)."""
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        conn.commit()
