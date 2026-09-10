"""Audit hardening (corrected DB-structure audit follow-up).

Fixes derived from point-by-point verification of the (stale) database-structure audit
against the live PostgreSQL database and the application source. Additive only —
nothing already present on PostgreSQL is re-created (no duplicate constraints), and no
application logic is changed; every new constraint matches a value set the application
already writes:

  - DROP assessments.rule_version_id: dead legacy column. Never written or read by the
    application (no ForeignKey in the ORM, no route/service touch it); rule-version
    lineage is captured by assessment_rule_results.rule_version_id.
  - FK discovered_sources.suggested_country_id -> countries.country_id (types match).
  - partial UNIQUE "at most one ACTIVE rule per (program_id, field)":
    rules (program_id, field) WHERE status = 'ACTIVE' (app already enforces this on
    activation; now hardened at the DB level).
  - status CHECK constraints matching application vocabularies:
      ck_extract_status       extraction_runs.status      RUNNING/COMPLETED/FAILED/DRY_RUN
      ck_pipeline_status      pipeline_runs.status        RUNNING/COMPLETED/FAILED/PARTIAL
      ck_source_status        government_sources.status   health-flag vocabulary (incl. BROKEN)
      ck_source_fetch_status  government_sources.fetch_status  FetchStatus enum (nullable)
      ck_discovered_status    discovered_sources.status   PENDING_REVIEW/APPROVED/REJECTED
  - missing index coverage for lineage FK columns (additive; no duplicates with the
    existing idx_* covering rule_id/program_id/etc.).

Revision ID: f8a9b0c1d2e3
Revises: f7a6d5c4b3a2

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f8a9b0c1d2e3'
down_revision: Union[str, Sequence[str], None] = 'f7a6d5c4b3a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Existence guards (idempotent; PostgreSQL catalogs)
# ---------------------------------------------------------------------------
def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = :name"
        ),
        {"name": name},
    ).fetchone() is not None


def _column_exists(conn, table: str, column: str) -> bool:
    return conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :table "
            "AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).fetchone() is not None


def _index_exists(conn, index_name: str) -> bool:
    return conn.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() AND indexname = :name"),
        {"name": index_name},
    ).fetchone() is not None


def _constraint_exists(conn, name: str) -> bool:
    return conn.execute(
        sa.text("SELECT 1 FROM pg_constraint WHERE conname = :name"),
        {"name": name},
    ).fetchone() is not None


def _drop_constraint(conn, table: str, name: str) -> None:
    if _constraint_exists(conn, name):
        op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"))


def _add_foreign_key(conn, name: str, table: str, column: str, ref_table: str, ref_column: str) -> None:
    if not _constraint_exists(conn, name):
        op.execute(
            sa.text(
                f"ALTER TABLE {table} ADD CONSTRAINT {name} "
                f"FOREIGN KEY ({column}) REFERENCES {ref_table} ({ref_column}) "
                "ON DELETE NO ACTION ON UPDATE NO ACTION"
            )
        )


def _add_check(conn, name: str, table: str, expression: str) -> None:
    if not _constraint_exists(conn, name):
        op.execute(sa.text(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expression})"))


def _add_index(conn, index_name: str, sql: str) -> None:
    if not _index_exists(conn, index_name):
        op.execute(sa.text(sql))


def _assert_no_violations(conn, label: str, sql: str, params: tuple = ()) -> None:
    rows = conn.execute(sa.text(sql), params).fetchall()
    if rows:
        raise RuntimeError(
            f"{label}: found {len(rows)} violating rows; refusing to apply constraint. "
            f"First rows: {[dict(r) for r in rows[:5]]}"
        )


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------
def upgrade() -> None:
    conn = op.get_bind()

    # 1) Drop the dead assessments.rule_version_id column (guarded; no app usage).
    if _table_exists(conn, "assessments") and _column_exists(conn, "assessments", "rule_version_id"):
        op.execute(sa.text("ALTER TABLE assessments DROP COLUMN rule_version_id"))

    # 2) FK discovered_sources.suggested_country_id -> countries.country_id.
    if _table_exists(conn, "discovered_sources") and _table_exists(conn, "countries"):
        _assert_no_violations(
            conn,
            "discovered_sources.suggested_country_id orphan check",
            "SELECT suggested_country_id, count(*) AS n FROM discovered_sources "
            "WHERE suggested_country_id NOT IN (SELECT country_id FROM countries) "
            "GROUP BY 1",
        )
        _add_foreign_key(
            conn,
            "fk_discovered_suggested_country",
            "discovered_sources",
            "suggested_country_id",
            "countries",
            "country_id",
        )

    # 3) Partial unique: at most one ACTIVE rule per (program_id, field).
    _add_index(
        conn,
        "uq_rules_active_per_field",
        "CREATE UNIQUE INDEX uq_rules_active_per_field "
        "ON rules (program_id, field) WHERE status = 'ACTIVE'",
    )

    # 4) Status CHECK constraints (application vocabularies).
    _add_check(
        conn, "ck_extract_status", "extraction_runs",
        "status IN ('RUNNING','COMPLETED','FAILED','DRY_RUN')",
    )
    _add_check(
        conn, "ck_pipeline_status", "pipeline_runs",
        "status IN ('RUNNING','COMPLETED','FAILED','PARTIAL')",
    )
    _add_check(
        conn, "ck_source_status", "government_sources",
        "status IN ('ACTIVE','DISABLED','ACCESS_BLOCKED','NETWORK_RESTRICTED',"
        "'REQUIRES_BROWSER','REQUIRES_MANUAL_DOWNLOAD','NEEDS_MANUAL_CAPTURE','BROKEN')",
    )
    _add_check(
        conn, "ck_source_fetch_status", "government_sources",
        "fetch_status IS NULL OR fetch_status IN "
        "('OK','FETCH_FAILED','ACCESS_BLOCKED','NETWORK_RESTRICTED',"
        "'REQUIRES_BROWSER','REQUIRES_MANUAL_DOWNLOAD','INVALID_CONTENT')",
    )
    _add_check(
        conn, "ck_discovered_status", "discovered_sources",
        "status IN ('PENDING_REVIEW','APPROVED','REJECTED')",
    )

    # 5) Missing index coverage for lineage FK columns (additive).
    _add_index(
        conn, "idx_extract_run_dv",
        "CREATE INDEX idx_extract_run_dv ON extraction_runs (document_version_id)",
    )
    _add_index(
        conn, "idx_rule_reqver",
        "CREATE INDEX idx_rule_reqver ON rules (requirement_version_id)",
    )
    _add_index(
        conn, "idx_rulever_reqver",
        "CREATE INDEX idx_rulever_reqver ON rule_versions (requirement_version_id)",
    )
    _add_index(
        conn, "idx_evidence_dv",
        "CREATE INDEX idx_evidence_dv ON requirement_evidence (document_version_id)",
    )
    _add_index(
        conn, "idx_evidence_doc",
        "CREATE INDEX idx_evidence_doc ON requirement_evidence (document_id)",
    )
    _add_index(
        conn, "idx_result_rulever",
        "CREATE INDEX idx_result_rulever ON assessment_rule_results (rule_version_id)",
    )
    _add_index(
        conn, "idx_assessment_ruleset",
        "CREATE INDEX idx_assessment_ruleset ON assessments (ruleset_id)",
    )
    _add_index(
        conn, "idx_req_dv",
        "CREATE INDEX idx_req_dv ON requirements (document_version_id)",
    )
    _add_index(
        conn, "idx_reqver_dv",
        "CREATE INDEX idx_reqver_dv ON requirement_versions (document_version_id)",
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Reverse step 5: indexes first.
    for index_name in (
        "idx_reqver_dv", "idx_req_dv", "idx_assessment_ruleset", "idx_result_rulever",
        "idx_evidence_doc", "idx_evidence_dv", "idx_rulever_reqver", "idx_rule_reqver",
        "idx_extract_run_dv",
    ):
        op.execute(sa.text(f"DROP INDEX IF EXISTS {index_name}"))

    # Reverse step 4: checks.
    for table, name in (
        ("discovered_sources", "ck_discovered_status"),
        ("government_sources", "ck_source_fetch_status"),
        ("government_sources", "ck_source_status"),
        ("pipeline_runs", "ck_pipeline_status"),
        ("extraction_runs", "ck_extract_status"),
    ):
        _drop_constraint(conn, table, name)

    # Reverse step 3: partial unique index.
    op.execute(sa.text("DROP INDEX IF EXISTS uq_rules_active_per_field"))

    # Reverse step 2: FK.
    if _constraint_exists(conn, "fk_discovered_suggested_country"):
        op.execute(sa.text("ALTER TABLE discovered_sources DROP CONSTRAINT IF EXISTS fk_discovered_suggested_country"))

    # Reverse step 1: restore the legacy nullable column.
    if _table_exists(conn, "assessments") and not _column_exists(conn, "assessments", "rule_version_id"):
        op.execute(sa.text("ALTER TABLE assessments ADD COLUMN rule_version_id INTEGER"))