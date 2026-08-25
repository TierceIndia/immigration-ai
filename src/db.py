"""SQLite connection + schema bootstrap for the Phase-1 source registry / document metadata store."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "immigration.db"
SCHEMA_PATH = ROOT / "schema" / "schema.sql"
PHASE2_SCHEMA_PATH = ROOT / "schema" / "phase2_requirements.sql"


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_schema(conn: sqlite3.Connection | None = None, db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Create all tables if they don't already exist. Safe to call repeatedly."""
    own_conn = conn is None
    if conn is None:
        conn = get_connection(db_path)
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(ddl)
    if PHASE2_SCHEMA_PATH.exists():
        conn.executescript(PHASE2_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


if __name__ == "__main__":
    c = init_schema()
    print(f"Schema initialized at {DB_PATH}")
    tables = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
    ).fetchall()
    for t in tables:
        print(" -", t["name"])
    c.close()
