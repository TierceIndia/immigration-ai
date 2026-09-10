"""Context-scoped DB session for code that receives only an engine.

``fetch_document_and_store`` and friends accept either a ``Session`` or an engine; when an
engine is passed they resolve the session through this module so tests can swap a mocked
session (patching ``db.current_session.current_session``) without touching the engine.
"""
from __future__ import annotations

import contextvars

from db.connection import get_session_factory

_current = contextvars.ContextVar("current_db_session", default=None)


def current_session(engine):
    """Return the session bound to *engine* for the current context, creating one if needed."""
    session = _current.get()
    if session is None or getattr(session, "bind", None) is not engine:
        session = get_session_factory(engine)()
        _current.set(session)
    return session


def reset_current_session() -> None:
    """Drop any session cached for the current context (used by tests/cleanup)."""
    _current.set(None)