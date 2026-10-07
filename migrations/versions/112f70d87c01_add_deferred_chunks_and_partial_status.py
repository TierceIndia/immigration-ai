"""Add extraction_runs.deferred_chunks and PARTIAL to its status vocabulary.

Part of the Gemini free-tier quota fix: when a chunk's LLM call hits a
persistent provider quota/rate-limit exhaustion, that one chunk is now
deferred (skipped, not fabricated-empty) instead of aborting the whole
document's extraction. deferred_chunks counts how many chunks in a run were
skipped this way, and status gets a new PARTIAL value (completed, but with
chunks that still need a retry on a later sync) alongside the existing
RUNNING/COMPLETED/FAILED/DRY_RUN vocabulary.

Additive only, matching f8a9b0c1d2e3's guarded style so it is safe to run
against a database that may already have been hand-patched.

Revision ID: 112f70d87c01
Revises: 7c9e2a4f1b6d

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '112f70d87c01'
down_revision: Union[str, Sequence[str], None] = '7c9e2a4f1b6d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    return conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :table "
            "AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).fetchone() is not None


def _constraint_exists(conn, name: str) -> bool:
    return conn.execute(
        sa.text("SELECT 1 FROM pg_constraint WHERE conname = :name"),
        {"name": name},
    ).fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    if not _column_exists(conn, "extraction_runs", "deferred_chunks"):
        op.execute(sa.text(
            "ALTER TABLE extraction_runs ADD COLUMN deferred_chunks "
            "INTEGER NOT NULL DEFAULT 0"
        ))

    if _constraint_exists(conn, "ck_extract_status"):
        op.execute(sa.text("ALTER TABLE extraction_runs DROP CONSTRAINT ck_extract_status"))
    op.execute(sa.text(
        "ALTER TABLE extraction_runs ADD CONSTRAINT ck_extract_status "
        "CHECK (status IN ('RUNNING','COMPLETED','FAILED','DRY_RUN','PARTIAL'))"
    ))


def downgrade() -> None:
    conn = op.get_bind()

    if _constraint_exists(conn, "ck_extract_status"):
        op.execute(sa.text("ALTER TABLE extraction_runs DROP CONSTRAINT ck_extract_status"))
    op.execute(sa.text(
        "UPDATE extraction_runs SET status = 'FAILED' WHERE status = 'PARTIAL'"
    ))
    op.execute(sa.text(
        "ALTER TABLE extraction_runs ADD CONSTRAINT ck_extract_status "
        "CHECK (status IN ('RUNNING','COMPLETED','FAILED','DRY_RUN'))"
    ))

    if _column_exists(conn, "extraction_runs", "deferred_chunks"):
        op.execute(sa.text("ALTER TABLE extraction_runs DROP COLUMN deferred_chunks"))
