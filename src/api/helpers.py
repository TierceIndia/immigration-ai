"""Shared API helpers — UUID validation, common utilities."""
from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException


def parse_uuid_or_404(id_str: str) -> UUID:
    """Parse a string to UUID, raising HTTP 404 if format is invalid."""
    try:
        return UUID(id_str)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
