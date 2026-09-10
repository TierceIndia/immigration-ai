"""Lineage + immutability + integrity hardening (audit item P0/P2).

Chain-of-custody enforcement (every record carries the exact immutable version id it was
derived from):

  extraction_runs.document_version_id  -> document_versions.version_id
  requirement_evidence.document_version_id -> document_versions.version_id + chunk lineage
  requirements.document_version_id     -> document_versions.version_id
  requirement_versions.document_version_id -> document_versions.version_id
  rules.requirement_version_id         -> requirement_versions.version_id
  rule_versions.requirement_version_id -> requirement_versions.version_id
  assessments.ruleset_id               -> rulesets.ruleset_id
  assessment_rule_results.rule_version_id -> rule_versions.version_id

Plus composite FKs that make impossible-lineage rows unmatable at the DB level, CHECK
constraints for the documented status/version/bool vocabularies, idempotency uniques,
immutable rulesets (new tables), program<->country consistency, monetary precision, and
append-only / fallback triggers (PostgreSQL; guarded so SQLite tests are unaffected).

Revision ID: f7a6d5c4b3a2
Revises: 0ceaaa268719

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7a6d5c4b3a2'
down_revision: Union[str, Sequence[str], None] = '0ceaaa268719'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Pre-flight integrity checks: each query must return zero rows, otherwise the
# migration aborts with a readable message instead of applying constraints onto
# corrupt data.
# ---------------------------------------------------------------------------
def _assert_no_violations(conn, label: str, sql: str, params: tuple = ()) -> None:
    rows = conn.execute(sa.text(sql), params).fetchall()
    if rows:
        raise RuntimeError(
            f"{label}: found {len(rows)} violating rows; refusing to apply constraint. "
            f"First rows: {[dict(r) for r in rows[:5]]}"
        )


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = :name"
        ),
        {"name": name},
    ).fetchone()
    return row is not None


def _column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :table "
            "AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).fetchone()
    return row is not None


def _add_foreign_key(conn, name, table, local_cols, ref_table, ref_cols) -> None:
    conn.execute(sa.text(
        f"ALTER TABLE {table} ADD CONSTRAINT {name} "
        f"FOREIGN KEY ({', '.join(local_cols)}) "
        f"REFERENCES {ref_table} ({', '.join(ref_cols)})"
    ))


def _add_check(conn, name, table, expression) -> None:
    conn.execute(sa.text(
        f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expression})"
    ))


def _preflight(conn) -> None:
    _assert_no_violations(
        conn, "chunks whose version belongs to another document",
        """
        SELECT c.chunk_id FROM document_chunks c
        JOIN document_versions v ON v.version_id = c.version_id
        WHERE v.document_id <> c.document_id
        """,
    )
    _assert_no_violations(
        conn, "extraction runs without a matching document version",
        """
        SELECT er.run_id FROM extraction_runs er
        LEFT JOIN document_versions v
          ON v.document_id = er.document_id AND v.version_number = er.document_version
        WHERE v.version_id IS NULL
        """,
    )
    _assert_no_violations(
        conn, "evidence without a matching document version",
        """
        SELECT e.evidence_id FROM requirement_evidence e
        LEFT JOIN document_versions v
          ON v.document_id = e.document_id AND v.version_number = e.document_version
        WHERE v.version_id IS NULL
        """,
    )
    _assert_no_violations(
        conn, "evidence chunk/doc/version mismatches",
        """
        SELECT e.evidence_id FROM requirement_evidence e
        LEFT JOIN document_chunks c ON c.chunk_id = e.chunk_id
        WHERE c.chunk_id IS NULL
           OR c.document_id <> e.document_id
           OR c.version_id <> (SELECT v.version_id FROM document_versions v
                                WHERE v.document_id = e.document_id
                                  AND v.version_number = e.document_version)
        """,
    )
    _assert_no_violations(
        conn, "requirements whose program/country contradict the program row",
        """
        SELECT r.requirement_id FROM requirements r
        JOIN immigration_programs p ON p.program_id = r.program_id
        WHERE p.country_id <> r.country_id
        """,
    )
    _assert_no_violations(
        conn, "documents whose program/country contradict the program row",
        """
        SELECT gd.document_id FROM government_documents gd
        JOIN immigration_programs p ON p.program_id = gd.program_id
        WHERE p.country_id <> gd.country_id
        """,
    )
    _assert_no_violations(
        conn, "government_sources whose program/country contradict the program row",
        """
        SELECT gs.source_id FROM government_sources gs
        JOIN immigration_programs p ON p.program_id = gs.program_id
        WHERE p.country_id <> gs.country_id
        """,
    )
    _assert_no_violations(
        conn, "rules whose program contradicts their parent requirement",
        """
        SELECT r.rule_id FROM rules r
        JOIN requirements q ON q.requirement_id = r.requirement_id
        WHERE q.program_id <> r.program_id
        """,
    )
    _assert_no_violations(
        conn, "requirements with unsupported status",
        """
        SELECT requirement_id FROM requirements
        WHERE status NOT IN
              ('DRAFT','VALIDATED','NEEDS_REVIEW','APPROVED','REJECTED','SUPERSEDED','CONFLICT')
        """,
    )
    _assert_no_violations(
        conn, "rules with unsupported status",
        """
        SELECT rule_id FROM rules
        WHERE status NOT IN
              ('DRAFT','VALIDATED','APPROVED','ACTIVE','SUPERSEDED','RETIRED','REJECTED')
        """,
    )
    _assert_no_violations(
        conn, "version numbers that are not positive",
        """
        SELECT version_id FROM (
            SELECT version_id, version_number, 1 AS tbl FROM document_versions
            UNION ALL
            SELECT version_id, version_number, 2 FROM requirement_versions
            UNION ALL
            SELECT version_id, version_number, 3 FROM rule_versions
        ) t WHERE version_number < 1
        """,
    )
    _assert_no_violations(
        conn, "assessments with unsupported business/overall status",
        """
        SELECT assessment_id FROM assessments
        WHERE business_status NOT IN ('ELIGIBLE','INELIGIBLE','INCOMPLETE')
           OR overall_status NOT IN ('PASS','FAIL','UNKNOWN','NOT_APPLICABLE','ERROR')
        """,
    )
    _assert_no_violations(
        conn, "assessment rule results with unsupported status",
        """
        SELECT result_id FROM assessment_rule_results
        WHERE status NOT IN ('PASS','FAIL','UNKNOWN','NOT_APPLICABLE','ERROR')
        """,
    )
    _assert_no_violations(
        conn, "government sources falling back to themselves",
        "SELECT source_id FROM government_sources WHERE fallback_source_id = source_id",
    )
    _assert_no_violations(
        conn, "duplicate document_versions (same document + content hash)",
        """
        SELECT document_id, content_hash FROM document_versions
        GROUP BY document_id, content_hash HAVING COUNT(*) > 1
        """,
    )
    _assert_no_violations(
        conn, "duplicate chunk order_index within a document version",
        """
        SELECT version_id, order_index FROM document_chunks
        GROUP BY version_id, order_index HAVING COUNT(*) > 1
        """,
    )
    _assert_no_violations(
        conn, "active-rule duplicate (program, semantic_key) pairs",
        """
        SELECT program_id, semantic_key FROM requirements
        WHERE status NOT IN ('CONFLICT','REJECTED')
        GROUP BY program_id, semantic_key HAVING COUNT(*) > 1
        """,
    )
    _assert_no_violations(
        conn, "duplicate rule_result per assessment+rule",
        """
        SELECT assessment_id, rule_id FROM assessment_rule_results
        GROUP BY assessment_id, rule_id HAVING COUNT(*) > 1
        """,
    )
    _assert_no_violations(
        conn, "rules with non-positive current_version",
        "SELECT rule_id FROM rules WHERE current_version < 1",
    )


# ---------------------------------------------------------------------------
# Immutability + fallback triggers (PostgreSQL only — SQLite test engines are
# schema-built from ORM metadata and must not receive DDL they cannot run).
# ---------------------------------------------------------------------------
def _enable_triggers() -> bool:
    bind = op.get_bind()
    return (bind.dialect.name == "postgresql")


_APPEND_ONLY_TABLES = (
    "document_versions",
    "requirement_versions",
    "rule_versions",
    "assessments",
    "assessment_rule_results",
    "rulesets",
    "ruleset_rules",
)


def _create_append_only_triggers() -> None:
    if not _enable_triggers():
        return
    conn = op.get_bind()
    conn.execute(sa.text(
        """
        CREATE OR REPLACE FUNCTION fn_block_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'row mutation (UPDATE/DELETE) on % is forbidden: history is append-only',
                TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    ))
    for table in _APPEND_ONLY_TABLES:
        conn.execute(sa.text(
            f"""
            CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION fn_block_mutation();
            """
        ))


def _create_fallback_trigger() -> None:
    if not _enable_triggers():
        return
    op.get_bind().execute(sa.text(
        """
        CREATE OR REPLACE FUNCTION fn_reject_fallback_self_ref() RETURNS trigger AS $$
        BEGIN
            IF NEW.fallback_source_id = NEW.source_id THEN
                RAISE EXCEPTION 'government source % cannot be its own fallback', NEW.source_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    ))
    op.get_bind().execute(sa.text(
        """
        CREATE TRIGGER trg_government_sources_no_self_fallback
        BEFORE INSERT OR UPDATE ON government_sources
        FOR EACH ROW EXECUTE FUNCTION fn_reject_fallback_self_ref();
        """
    ))


def _drop_triggers() -> None:
    if not _enable_triggers():
        return
    conn = op.get_bind()
    for table in _APPEND_ONLY_TABLES:
        try:
            conn.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table};"))
        except Exception:
            pass
    try:
        conn.execute(sa.text(
            "DROP TRIGGER IF EXISTS trg_government_sources_no_self_fallback ON government_sources;"
        ))
    except Exception:
        pass


def upgrade() -> None:
    conn = op.get_bind()
    _preflight(conn)

    # ------------------------------------------------------------------ #
    # 1. Monetary precision
    # ------------------------------------------------------------------ #
    with op.batch_alter_table('llm_usage', schema=None) as batch_op:
        batch_op.alter_column(
            'cost_usd', existing_type=sa.Float(), type_=sa.Numeric(16, 6))
    with op.batch_alter_table('llm_budget_log', schema=None) as batch_op:
        batch_op.alter_column(
            'total_cost_usd', existing_type=sa.Float(), type_=sa.Numeric(16, 6))
        batch_op.alter_column(
            'budget_limit_usd', existing_type=sa.Float(), type_=sa.Numeric(16, 6))

    # ------------------------------------------------------------------ #
    # 2. New canonical version-id columns (nullable; writers populate them)
    # ------------------------------------------------------------------ #
    with op.batch_alter_table('extraction_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('document_version_id', sa.Integer(), nullable=True))
    with op.batch_alter_table('requirements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('document_version_id', sa.Integer(), nullable=True))
    with op.batch_alter_table('requirement_evidence', schema=None) as batch_op:
        batch_op.add_column(sa.Column('document_version_id', sa.Integer(), nullable=True))
    with op.batch_alter_table('requirement_versions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('document_version_id', sa.Integer(), nullable=True))
    with op.batch_alter_table('rules', schema=None) as batch_op:
        batch_op.add_column(sa.Column('requirement_version_id', sa.Integer(), nullable=True))
    with op.batch_alter_table('rule_versions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('requirement_version_id', sa.Integer(), nullable=True))
    with op.batch_alter_table('assessment_rule_results', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rule_version_id', sa.Integer(), nullable=True))

    # ------------------------------------------------------------------ #
    # 3. Rulesets (immutable ruleset + member rule_versions)
    #    The rulesets/ruleset_rules tables may already exist (provisioned ad hoc during
    #    exploratory work before this migration shipped); their DDL matches this
    #    migration's exactly, so we create only when absent.
    # ------------------------------------------------------------------ #
    if not _table_exists(conn, "rulesets"):
        op.create_table('rulesets',
            sa.Column('ruleset_id', sa.String(length=36), nullable=False),
            sa.Column('program_id', sa.String(length=32), nullable=False),
            sa.Column('content_hash', sa.String(length=64), nullable=False),
            sa.Column('status', sa.String(length=16), nullable=False),
            sa.Column('released_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('released_by', sa.String(length=64), nullable=False),
            sa.Column('rule_count', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("status IN ('DRAFT','RELEASED')", name='ck_ruleset_status'),
            sa.ForeignKeyConstraint(['program_id'], ['immigration_programs.program_id'], ),
            sa.PrimaryKeyConstraint('ruleset_id'),
            sa.UniqueConstraint('program_id', 'content_hash', name='uq_ruleset_content')
        )
    if not _table_exists(conn, "ruleset_rules"):
        op.create_table('ruleset_rules',
            sa.Column('ruleset_id', sa.String(length=36), nullable=False),
            sa.Column('rule_version_id', sa.Integer(), nullable=False),
            sa.Column('position', sa.Integer(), nullable=False),
            sa.CheckConstraint('position >= 0', name='ck_ruleset_rule_position'),
            sa.ForeignKeyConstraint(['ruleset_id'], ['rulesets.ruleset_id'], ),
            sa.ForeignKeyConstraint(['rule_version_id'], ['rule_versions.version_id'], ),
            sa.PrimaryKeyConstraint('ruleset_id', 'rule_version_id'),
            sa.UniqueConstraint('ruleset_id', 'position', name='uq_ruleset_rule_position')
        )

    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_ruleset_rule_version ON ruleset_rules(rule_version_id);"
    ))

    if not _column_exists(conn, "assessments", "ruleset_id"):
        with op.batch_alter_table('assessments', schema=None) as batch_op:
            batch_op.add_column(sa.Column('ruleset_id', sa.String(length=36), nullable=True))

    # ------------------------------------------------------------------ #
    # 4. Unique targets required by the composite FKs below
    # ------------------------------------------------------------------ #
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_ver_docid_versionid "
        "ON document_versions(document_id, version_id);"
    ))
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_ver_content "
        "ON document_versions(document_id, content_hash);"
    ))
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_gov_src_program_country "
        "ON immigration_programs(program_id, country_id);"
    ))
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_req_program_pair "
        "ON requirements(requirement_id, program_id);"
    ))
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_chunk_version_order "
        "ON document_chunks(version_id, order_index);"
    ))
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_chunk_lineage "
        "ON document_chunks(chunk_id, document_id, version_id);"
    ))
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_req_active_semantic ON requirements "
        "(program_id, semantic_key) "
        "WHERE status NOT IN ('CONFLICT','REJECTED');"
    ))
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_result_assessment_rule "
        "ON assessment_rule_results(assessment_id, rule_id);"
    ))

    # ------------------------------------------------------------------ #
    # 5. Backfill canonical version ids on existing rows
    # ------------------------------------------------------------------ #
    conn.execute(sa.text("""
        UPDATE extraction_runs er
        SET document_version_id = v.version_id
        FROM document_versions v
        WHERE v.document_id = er.document_id AND v.version_number = er.document_version
    """))
    conn.execute(sa.text("""
        UPDATE requirement_evidence e
        SET document_version_id = v.version_id
        FROM document_versions v
        WHERE v.document_id = e.document_id AND v.version_number = e.document_version
    """))
    conn.execute(sa.text("""
        UPDATE requirements r
        SET document_version_id = er.document_version_id
        FROM extraction_runs er
        WHERE er.run_id = r.extraction_run_id
    """))
    conn.execute(sa.text("""
        UPDATE requirement_versions rv
        SET document_version_id = er.document_version_id
        FROM requirements r
        JOIN extraction_runs er ON er.run_id = r.extraction_run_id
        WHERE r.requirement_id = rv.requirement_id
    """))
    conn.execute(sa.text("""
        UPDATE rules r
        SET requirement_version_id = (
            SELECT rv.version_id FROM requirement_versions rv
            WHERE rv.requirement_id = r.requirement_id
            ORDER BY rv.version_number DESC
            LIMIT 1
        )
    """))
    conn.execute(sa.text("""
        UPDATE rule_versions rv
        SET requirement_version_id = r.requirement_version_id
        FROM rules r
        WHERE r.rule_id = rv.rule_id
    """))
    conn.execute(sa.text("""
        UPDATE assessment_rule_results a
        SET rule_version_id = (
            SELECT rv.version_id FROM rule_versions rv
            WHERE rv.rule_id = a.rule_id
            ORDER BY rv.version_number DESC
            LIMIT 1
        )
    """))

    # ------------------------------------------------------------------ #
    # 6. Foreign keys (raw DDL: Alembic's create_foreign_key path crashes
    #    under this env with `TypeError: unhashable type: 'list'`).
    # ------------------------------------------------------------------ #
    _add_foreign_key(conn, 'fk_chunks_doc_version_pair', 'document_chunks',
                     ['document_id', 'version_id'], 'document_versions', ['document_id', 'version_id'])
    _add_foreign_key(conn, 'fk_extract_run_version', 'extraction_runs',
                     ['document_version_id'], 'document_versions', ['version_id'])
    _add_foreign_key(conn, 'fk_extract_run_doc_version_pair', 'extraction_runs',
                     ['document_id', 'document_version'], 'document_versions', ['document_id', 'version_number'])
    _add_foreign_key(conn, 'fk_evidence_version', 'requirement_evidence',
                     ['document_version_id'], 'document_versions', ['version_id'])
    _add_foreign_key(conn, 'fk_evidence_chunk_lineage', 'requirement_evidence',
                     ['chunk_id', 'document_id', 'document_version_id'], 'document_chunks',
                     ['chunk_id', 'document_id', 'version_id'])
    _add_foreign_key(conn, 'fk_evidence_doc_version_pair', 'requirement_evidence',
                     ['document_id', 'document_version'], 'document_versions', ['document_id', 'version_number'])
    _add_foreign_key(conn, 'fk_req_version', 'requirements',
                     ['document_version_id'], 'document_versions', ['version_id'])
    _add_foreign_key(conn, 'fk_req_program_country', 'requirements',
                     ['program_id', 'country_id'], 'immigration_programs', ['program_id', 'country_id'])
    _add_foreign_key(conn, 'fk_reqver_version', 'requirement_versions',
                     ['document_version_id'], 'document_versions', ['version_id'])
    _add_foreign_key(conn, 'fk_rule_req_version', 'rules',
                     ['requirement_version_id'], 'requirement_versions', ['version_id'])
    _add_foreign_key(conn, 'fk_rule_req_program_pair', 'rules',
                     ['requirement_id', 'program_id'], 'requirements', ['requirement_id', 'program_id'])
    _add_foreign_key(conn, 'fk_rulever_req_version', 'rule_versions',
                     ['requirement_version_id'], 'requirement_versions', ['version_id'])
    _add_foreign_key(conn, 'fk_assessment_ruleset', 'assessments',
                     ['ruleset_id'], 'rulesets', ['ruleset_id'])
    _add_foreign_key(conn, 'fk_result_rule_version', 'assessment_rule_results',
                     ['rule_version_id'], 'rule_versions', ['version_id'])
    _add_foreign_key(conn, 'fk_src_program_country', 'government_sources',
                     ['program_id', 'country_id'], 'immigration_programs', ['program_id', 'country_id'])
    _add_foreign_key(conn, 'fk_doc_program_country', 'government_documents',
                     ['program_id', 'country_id'], 'immigration_programs', ['program_id', 'country_id'])

    # ------------------------------------------------------------------ #
    # 7. CHECK constraints (raw DDL — same Alembic constraint bug)
    # ------------------------------------------------------------------ #
    _add_check(conn, 'ck_doc_version_positive', 'document_versions', 'version_number > 0')
    _add_check(conn, 'ck_doc_change_type', 'document_versions',
               "change_type IN ('INITIAL','CHANGED','UNCHANGED','MANUAL_CAPTURE')")
    _add_check(conn, 'ck_chunk_order_nonneg', 'document_chunks', 'order_index >= 0')
    _add_check(conn, 'ck_extract_version_positive', 'extraction_runs', 'document_version > 0')
    _add_check(conn, 'ck_extract_dry_run', 'extraction_runs', 'dry_run IN (0,1)')
    _add_check(conn, 'ck_evidence_version_positive', 'requirement_evidence', 'document_version > 0')
    _add_check(conn, 'ck_evidence_verified', 'requirement_evidence', 'verified IN (0,1)')
    _add_check(conn, 'ck_req_status', 'requirements',
               "status IN ('DRAFT','VALIDATED','NEEDS_REVIEW','APPROVED','REJECTED',"
               "'SUPERSEDED','CONFLICT')")
    _add_check(conn, 'ck_req_current_version', 'requirements', 'current_version > 0')
    _add_check(conn, 'ck_req_mandatory', 'requirements', 'mandatory IN (0,1)')
    _add_check(conn, 'ck_reqver_version_number', 'requirement_versions', 'version_number > 0')
    _add_check(conn, 'ck_reqver_docver', 'requirement_versions', 'document_version >= 0')
    _add_check(conn, 'ck_rule_status', 'rules',
               "status IN ('DRAFT','VALIDATED','APPROVED','ACTIVE','SUPERSEDED','RETIRED','REJECTED')")
    _add_check(conn, 'ck_rule_current_version', 'rules', 'current_version > 0')
    _add_check(conn, 'ck_rulever_version_number', 'rule_versions', 'version_number > 0')
    _add_check(conn, 'ck_assessment_business', 'assessments',
               "business_status IN ('ELIGIBLE','INELIGIBLE','INCOMPLETE')")
    _add_check(conn, 'ck_assessment_overall', 'assessments',
               "overall_status IN ('PASS','FAIL','UNKNOWN','NOT_APPLICABLE','ERROR')")
    _add_check(conn, 'ck_result_status', 'assessment_rule_results',
               "status IN ('PASS','FAIL','UNKNOWN','NOT_APPLICABLE','ERROR')")
    _add_check(conn, 'ck_fetch_changed', 'fetch_log', 'changed IN (0,1)')

    # ------------------------------------------------------------------ #
    # 8. Triggers (PG only)
    # ------------------------------------------------------------------ #
    _create_fallback_trigger()
    _create_append_only_triggers()


def downgrade() -> None:
    # Reverse in the exact opposite order of upgrade.
    _drop_triggers()

    for table, constraint in (
        ('fetch_log', 'ck_fetch_changed'),
        ('assessment_rule_results', 'ck_result_status'),
        ('assessments', 'ck_assessment_overall'),
        ('assessments', 'ck_assessment_business'),
        ('rule_versions', 'ck_rulever_version_number'),
        ('rules', 'ck_rule_current_version'),
        ('rules', 'ck_rule_status'),
        ('requirement_versions', 'ck_reqver_docver'),
        ('requirement_versions', 'ck_reqver_version_number'),
        ('requirements', 'ck_req_mandatory'),
        ('requirements', 'ck_req_current_version'),
        ('requirements', 'ck_req_status'),
        ('requirement_evidence', 'ck_evidence_verified'),
        ('requirement_evidence', 'ck_evidence_version_positive'),
        ('extraction_runs', 'ck_extract_dry_run'),
        ('extraction_runs', 'ck_extract_version_positive'),
        ('document_chunks', 'ck_chunk_order_nonneg'),
        ('document_versions', 'ck_doc_change_type'),
        ('document_versions', 'ck_doc_version_positive'),
    ):
        try:
            op.execute(sa.text(
                f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}"))
        except Exception:
            pass

    for table, constraint in (
        ('government_documents', 'fk_doc_program_country'),
        ('government_sources', 'fk_src_program_country'),
        ('assessment_rule_results', 'fk_result_rule_version'),
        ('assessments', 'fk_assessment_ruleset'),
        ('rule_versions', 'fk_rulever_req_version'),
        ('rules', 'fk_rule_req_program_pair'),
        ('rules', 'fk_rule_req_version'),
        ('requirement_versions', 'fk_reqver_version'),
        ('requirements', 'fk_req_program_country'),
        ('requirements', 'fk_req_version'),
        ('requirement_evidence', 'fk_evidence_doc_version_pair'),
        ('requirement_evidence', 'fk_evidence_chunk_lineage'),
        ('requirement_evidence', 'fk_evidence_version'),
        ('extraction_runs', 'fk_extract_run_doc_version_pair'),
        ('extraction_runs', 'fk_extract_run_version'),
        ('document_chunks', 'fk_chunks_doc_version_pair'),
    ):
        try:
            op.execute(sa.text(
                f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}"))
        except Exception:
            pass

    for table, column in (
        ('extraction_runs', 'document_version_id'),
        ('requirements', 'document_version_id'),
        ('requirement_evidence', 'document_version_id'),
        ('requirement_versions', 'document_version_id'),
        ('rules', 'requirement_version_id'),
        ('rule_versions', 'requirement_version_id'),
        ('assessment_rule_results', 'rule_version_id'),
    ):
        try:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_column(column)
        except Exception:
            pass

    with op.batch_alter_table('assessments', schema=None) as batch_op:
        batch_op.drop_column('ruleset_id')

    for index in (
        'uq_doc_ver_docid_versionid', 'uq_doc_ver_content', 'uq_gov_src_program_country',
        'uq_req_program_pair', 'uq_chunk_version_order', 'uq_chunk_lineage',
        'uq_req_active_semantic', 'uq_result_assessment_rule', 'idx_ruleset_rule_version',
    ):
        try:
            op.drop_index(index)
        except Exception:
            pass

    op.drop_table('ruleset_rules')
    op.drop_table('rulesets')

    with op.batch_alter_table('llm_budget_log', schema=None) as batch_op:
        batch_op.alter_column('budget_limit_usd', existing_type=sa.Numeric(16, 6),
                              type_=sa.Float())
        batch_op.alter_column('total_cost_usd', existing_type=sa.Numeric(16, 6),
                              type_=sa.Float())
    with op.batch_alter_table('llm_usage', schema=None) as batch_op:
        batch_op.alter_column('cost_usd', existing_type=sa.Numeric(16, 6), type_=sa.Float())