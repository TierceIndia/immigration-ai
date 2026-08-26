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


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL not set. Copy .env.example to .env and configure."
        )
    return url


def get_engine(url: str | None = None):
    if url is None:
        url = get_database_url()
    return create_engine(
        url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=os.getenv("SQL_ECHO", "0") == "1",
    )


def get_session_factory(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def get_session(engine) -> Session:
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


def enable_uuid_extension(engine) -> None:
    """Enable pgcrypto for gen_random_uuid() (idempotent)."""
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        conn.commit()
