"""Regenerate DBSTRUCTURE.md from a live PostgreSQL database.

The schema snapshot in DBSTRUCTURE.md used to be produced by a removed
introspection route, so it drifted. This script is the replacement: point it at
the same DATABASE_URL the app uses (after ``alembic upgrade head``) and it
writes the tables, columns, keys, indexes and append-only triggers it actually
finds.

    python scripts/db_structure.py                  # writes DBSTRUCTURE.md
    python scripts/db_structure.py --out /tmp/x.md  # somewhere else

Read-only. It never touches application data beyond ``SELECT COUNT(*)``, and it
prints no credentials. The ``embedding`` column is reported as
``vector(1536)`` because SQLAlchemy's stock inspector does not know pgvector.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402

MIGRATIONS_DIR = ROOT / "migrations" / "versions"

MIGRATION_SUMMARIES = {
    "49b9088f3a03": "Initial schema from the ORM models.",
    "0ceaaa268719": "Schema verification (constraints, indexes, triggers).",
    "a1b2c3d4e5f6": "`government_sources.last_successful_fetch_at`.",
    "c7d8e9f0a1b2": "`discovered_sources` table (source auto-discovery candidates).",
    "d1f2e3a4b5c6": "Fallback strategy fields on `government_sources`.",
    "e4f6a1b2c3d4": "Explicit fetch-failure classification (`fetch_log.error_type`).",
    "f7a6d5c4b3a2": "Lineage, append-only triggers and integrity hardening.",
    "f8a9b0c1d2e3": "Audit hardening follow-up (constraints and indexes).",
    "a3c5e7f9b1d3": "Widen `requirement_id`/`rule_id` to VARCHAR(96) to stop silent truncation.",
    "7c9e2a4f1b6d": "Widen ACTIVE-rule uniqueness to (program, category, field).",
    "c7d8e9f0a1b3": "`government_sources.content_kind` (PAGE | NEWS_LIST).",
    "f4a5b6c7d8e9": "Persistent `extraction_cache` for chunk-level LLM results.",
    "e3f4a5b6c7d8": "`government_sources.extract_requirements` scope flag (reference-only sources).",
    "d8e9f0a1b2c4": "Allow `fetch_status = OK_EMPTY_CONTENT`.",
    "112f70d87c01": "`extraction_runs.deferred_chunks` and the PARTIAL status.",
    "0dbea9e27e35": "`scheduler_settings` (admin-editable schedule override).",
    "a5b6c7d8e9f0": "`extraction_runs.stored_new`/`merged_into_existing` metrics.",
    "b6c7d8e9f0a1": "`pipeline_runs.stored_new` metric.",
    "b7c8d9e0f1a2": "`host_cooldowns` (pause a host after a 403/429 refusal).",
    "c1d2e3f4a5b6": "Conditional-GET validators (`etag`, `last_modified`).",
    "b1c2d3e4f5a6": "First batch of modelled applicant concept fields.",
    "d2e3f4a5b6c7": "`requirements.concept` (closed vocabulary, NOT_MODELED label).",
    "c2d3e4f5a6b7": "pgvector `document_chunks.embedding` + HNSW cosine index.",
}


def _load_url() -> str:
    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("DATABASE_URL is not set (checked the environment and .env)")
    if "sqlite" in url:
        raise SystemExit("This script needs PostgreSQL (pgvector types are not introspectable on SQLite).")
    return url


def _migration_chain() -> list[tuple[str, str]]:
    nodes: dict[str, tuple[str, str]] = {}
    for path in MIGRATIONS_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8", errors="ignore")
        rev = re.search(r'^revision(?::[^=\n]+)?\s*=\s*["\']([^"\']+)["\']', source, re.M)
        down = re.search(
            r'^down_revision(?::[^=\n]+)?\s*=\s*(?:["\']([^"\']+)["\']|None)', source, re.M
        )
        if not rev:
            continue
        nodes[rev.group(1)] = (path.name, down.group(1) if down and down.group(1) else "")
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    current = next((r for r, (_, d) in nodes.items() if d == ""), "")
    while current and current in nodes and current not in seen:
        seen.add(current)
        ordered.append((current, nodes[current][0]))
        children = [r for r, (_, d) in nodes.items() if d == current]
        current = children[0] if children else ""
    for rev, (filename, _) in nodes.items():
        if rev not in seen:
            ordered.append((rev, filename))
    return ordered


def _column_type(column: dict) -> str:
    name = column["name"]
    if name == "embedding":
        return "vector(1536)"
    return str(column["type"]).lower()


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|")


def render(url: str) -> str:
    engine = create_engine(url)
    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names())

    with engine.connect() as conn:
        head = conn.execute(text("select version_num from alembic_version")).scalar()
        row_counts = {
            table: conn.execute(text(f'select count(*) from "{table}"')).scalar()
            for table in tables
        }
        indexes = conn.execute(
            text(
                """
                select tablename, indexname, indexdef
                from pg_indexes
                where schemaname = 'public'
                order by tablename, indexname
                """
            )
        ).all()
        triggers = conn.execute(
            text(
                """
                select event_object_table, trigger_name,
                       string_agg(action_timing || ' ' || event_manipulation, ', ' order by event_manipulation)
                from information_schema.triggers
                where trigger_schema = 'public'
                group by event_object_table, trigger_name
                order by event_object_table, trigger_name
                """
            )
        ).all()

    out: list[str] = []
    out.append("# Database Structure")
    out.append("")
    out.append(f"**Regenerated**: {date.today().isoformat()} with `python scripts/db_structure.py` from a")
    out.append("live PostgreSQL 16 + pgvector database at the current alembic head. The table and column")
    out.append("lists are introspected, not hand-written; row counts are whatever that database held at")
    out.append("generation time. Re-run the script after a migration instead of editing this file.")
    out.append("")
    out.append(f"- Alembic head: `{head}`")
    out.append(f"- Application tables: {len(tables)}")
    out.append("- `document_chunks.embedding` is pgvector's `vector(1536)`; SQLAlchemy's stock")
    out.append("  inspector reports it as an unknown `NULL` type, so it is labelled explicitly here.")
    out.append("")

    out.append("## Migrations (applied in order)")
    out.append("")
    out.append("| # | Revision | File | Purpose |")
    out.append("|---|---|---|---|")
    for number, (revision, filename) in enumerate(_migration_chain(), start=1):
        marker = " **(head)**" if revision == head else ""
        summary = MIGRATION_SUMMARIES.get(revision, "(see the migration docstring)")
        out.append(f"| {number} | `{revision}`{marker} | `{filename}` | {_cell(summary)} |")
    out.append("")

    out.append("## Tables")
    out.append("")
    out.append("| Table | Columns | Rows at generation |")
    out.append("|---|---|---|")
    for table in tables:
        out.append(f"| {table} | {len(inspector.get_columns(table))} | {row_counts[table]} |")
    out.append("")

    out.append("## Keys and indexes")
    out.append("")
    out.append("Generated primary-key indexes (`*_pkey`, `alembic_version_pkc`) and plain")
    out.append("single/multi-column btree indexes are omitted; every table has a primary key, and")
    out.append("the btree indexes are visible in the migrations. Listed here: unique constraints,")
    out.append("partial (`WHERE`) indexes and vector (HNSW) indexes.")
    out.append("")
    out.append("| Table | Index | Definition |")
    out.append("|---|---|---|")
    for table, index, definition in indexes:
        interesting = (
            "UNIQUE" in definition
            or " WHERE " in definition
            or "hnsw" in definition.lower()
            or "ivfflat" in definition.lower()
        )
        if not interesting:
            continue
        out.append(f"| {table} | `{index}` | `{_cell(definition)}` |")
    out.append("")

    out.append("## Foreign keys")
    out.append("")
    out.append("| Table | Column | References | On delete |")
    out.append("|---|---|---|---|")
    for table in tables:
        for fk in inspector.get_foreign_keys(table):
            out.append(
                f"| {table} | {', '.join(fk['constrained_columns'])} | "
                f"{fk['referred_table']}({', '.join(fk['referred_columns'])}) | {fk.get('options', {}).get('ondelete', '')} |"
            )
    out.append("")

    out.append("## Append-only triggers")
    out.append("")
    out.append("These triggers raise on UPDATE/DELETE, so historical assessments and rule versions cannot")
    out.append("be rewritten. The only supported way to bypass them is the test/cleanup pattern of")
    out.append("`ALTER TABLE ... DISABLE TRIGGER ALL` inside an explicit maintenance transaction.")
    out.append("")
    out.append("| Table | Trigger | Timing / events |")
    out.append("|---|---|---|")
    for table, trigger, events in triggers:
        out.append(f"| {table} | `{trigger}` | {events} |")
    out.append("")

    out.append("## Column detail")
    out.append("")
    for table in tables:
        out.append(f"### {table}")
        out.append("")
        out.append("| Column | Type | Nullable | Primary key |")
        out.append("|---|---|---|---|")
        for column in inspector.get_columns(table):
            out.append(
                f"| {column['name']} | {_column_type(column)} | "
                f"{'yes' if column['nullable'] else ''} | "
                f"{'PK' if column.get('primary_key') else ''} |"
            )
        out.append("")

    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "DBSTRUCTURE.md"))
    args = parser.parse_args()
    markdown = render(_load_url())
    Path(args.out).write_text(markdown, encoding="utf-8", newline="\n")
    print(f"wrote {args.out} ({len(markdown.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

