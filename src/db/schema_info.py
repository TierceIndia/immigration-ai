"""Database structure introspection — read-only schema report.

Used by the admin schema-report endpoint (and the checked-in DBSTRUCTURE.md).

Dialect-aware:
  * PostgreSQL -> information_schema (tables, columns, PKs, FKs).
  * SQLite     -> sqlite_master + PRAGMA table_info / foreign_key_list (used by
    the hermetic in-memory test engines).

No writes are performed: only introspection + ``SELECT COUNT(*)`` per table.
"""
from __future__ import annotations

from sqlalchemy import text


def _table_names_postgres(conn) -> list[str]:
    rows = conn.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    )).fetchall()
    return [r[0] for r in rows]


def _table_names_sqlite(conn) -> list[str]:
    rows = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )).fetchall()
    return [r[0] for r in rows]


def _columns_postgres(conn) -> dict[str, list[dict]]:
    rows = conn.execute(text(
        """
        SELECT t.table_name, c.column_name,
               c.data_type || COALESCE('('||c.character_maximum_length||')', '') AS type,
               c.is_nullable,
               c.ordinal_position
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema AND t.table_name = c.table_name
        WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_name, c.ordinal_position
        """
    )).fetchall()
    out: dict[str, list[dict]] = {}
    for table_name, name, type_, nullable, _pos in rows:
        out.setdefault(table_name, []).append({
            "name": name,
            "type": type_,
            "nullable": nullable == "YES",
        })
    return out


def _columns_sqlite(conn, tables: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for table in tables:
        rows = conn.execute(text(f'PRAGMA table_info("{table}")')).fetchall()
        out[table] = [
            {"name": r[1], "type": r[2], "nullable": not bool(r[3]), "pk": bool(r[5])}
            for r in rows
        ]
    return out


def _foreign_keys_postgres(conn) -> list[str]:
    rows = conn.execute(text(
        """
        SELECT tc.table_name, kcu.column_name, ccu.table_name, ccu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY'
        ORDER BY tc.table_name, kcu.column_name
        """
    )).fetchall()
    return [f"{t1}.{c1} -> {t2}.{c2}" for t1, c1, t2, c2 in rows]


def _foreign_keys_sqlite(conn, tables: list[str]) -> list[str]:
    out: list[str] = []
    for table in tables:
        rows = conn.execute(text(f'PRAGMA foreign_key_list("{table}")')).fetchall()
        for r in rows:
            from_col, ref_table, ref_col = r[3], r[2], r[4]
            out.append(f"{table}.{from_col} -> {ref_table}.{ref_col}")
    return sorted(out)


def _primary_keys_postgres(conn) -> list[str]:
    rows = conn.execute(text(
        """
        SELECT tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
        WHERE tc.constraint_type = 'PRIMARY KEY'
        ORDER BY tc.table_name, kcu.column_name
        """
    )).fetchall()
    return [f"{t}.{c}" for t, c in rows]


def tables_snapshot(engine) -> dict:
    """Return a full read-only structure snapshot of the database."""
    dialect = engine.dialect.name
    with engine.connect() as conn:
        tables = (
            _table_names_postgres(conn)
            if dialect == "postgresql"
            else _table_names_sqlite(conn)
        )
        if dialect == "postgresql":
            columns = _columns_postgres(conn)
            fks = _foreign_keys_postgres(conn)
            pks = _primary_keys_postgres(conn)
        else:
            columns = _columns_sqlite(conn, tables)
            fks = _foreign_keys_sqlite(conn, tables)
            pks = []
            for table, cols in columns.items():
                pks.extend(f"{table}.{c['name']}" for c in cols if c.get("pk"))

        # Exact row counts (skip the alembic_version tracking table).
        counts: dict[str, int] = {}
        for table in tables:
            if table == "alembic_version":
                continue
            try:
                row = conn.execute(text(f'SELECT COUNT(*) AS n FROM "{table}"')).fetchone()
                counts[table] = int(row[0])
            except Exception:
                counts[table] = -1

        # Migration version if tracked (SQLite test engines may lack the table).
        version = None
        if "alembic_version" in tables:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
            version = row[0] if row else None

    tables_out = [
        {
            "name": table,
            "column_count": len(columns.get(table, [])),
            "columns": columns.get(table, []),
            "rows": counts.get(table, 0),
        }
        for table in tables
    ]

    return {
        "dialect": dialect,
        "table_count": len(tables_out),
        "alembic_version": version,
        "primary_keys": sorted(pks),
        "foreign_keys": sorted(fks),
        "tables": tables_out,
    }