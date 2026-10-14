"""Shared safety guard for the Postgres-gated e2e tests in this directory.

These tests write and delete real rows - including temporarily disabling
append-only audit triggers (rule_versions, requirement_versions,
document_versions, assessments, ...) - against whatever database
POSTGRES_E2E_URL resolves to. They must never run against the shared main
dev/production database, which holds real government-sync output, real
admin review history, and real activated rules for the same program_ids
these tests exercise (SKILLED_189).

Found live: before this guard existed, both e2e files defaulted straight at
the main database, and one of them (test_full_knowledge_to_assessment.py)
disabled audit-immutability triggers on it on every run.
"""
from __future__ import annotations

from urllib.parse import urlparse

# The shared dev/production database name. Any e2e URL resolving to this
# must be refused outright - the e2e suite needs its own dedicated database
# (e.g. "immigration_e2e"), never this one.
MAIN_DATABASE_NAME = "immigration"


def refuse_if_main_database(url: str) -> None:
    """Raise if ``url``'s database name is the shared main database.

    Called at module import time in each e2e test file (before any fixture,
    any query, any DISABLE TRIGGER statement) so a misconfigured
    POSTGRES_E2E_URL fails loudly and immediately, not after it has already
    touched real data.
    """
    db_name = urlparse(url).path.lstrip("/")
    if db_name == MAIN_DATABASE_NAME:
        raise RuntimeError(
            f"POSTGRES_E2E_URL resolves to the main {MAIN_DATABASE_NAME!r} "
            f"database ({url!r}). The e2e suite disables audit-immutability "
            f"triggers and deletes rows - it must run against its own "
            f"dedicated database (e.g. 'immigration_e2e'), never the shared "
            f"dev/production one. Set POSTGRES_E2E_URL to a different "
            f"database name, or unset it to use this file's own default."
        )
