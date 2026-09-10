"""sqlite3-shaped adapter over a SQLAlchemy Session for Phase-2's legacy data access.

Phase-2 modules (``requirement_repository.py``, ``review.py``) were written against a raw
``sqlite3`` connection: ``?``-parameterized SQL, dictionary rows, and explicit commits. They
remain the only writers of the requirement knowledge-model tables and must run unchanged
against the real engine (PostgreSQL in production, SQLite in tests).

``SqliteConn`` presents that same surface over an ORM ``Session``:

* ``?`` placeholders are compiled to the bound dialect (left alone on SQLite, ``%s`` on
  PostgreSQL).
* Result rows are wrapped in dict-style rows (``row["col"]``, ``dict(row)``) regardless of
  the underlying DBAPI.
* Parameters are coerced so SQLite and PostgreSQL agree (``bool`` -> ``int``, datetimes stay
  native).
* A small resolver drops columns that exist in the legacy schema but not in the ORM schema
  (e.g. ``extraction_runs.started_at``) plus the matching VALUES expression and bound
  parameter, so the same SQL works against both schema shapes.

This is a compatibility shim only — new code must use the ORM directly.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------
class SqliteRow:
    """Dict-style row supporting ``row["col"]``, ``row[0]``, ``.keys()``, ``dict(row)``."""

    __slots__ = ("_keys", "_values", "_map")

    def __init__(self, keys: Iterable[str], values: Iterable[Any]):
        self._keys = list(keys)
        self._values = tuple(values)
        self._map = {k: i for i, k in enumerate(self._keys)}

    def keys(self) -> list[str]:
        return self._keys

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        idx = self._map.get(key)
        if idx is None:
            raise KeyError(key)
        return self._values[idx]

    def get(self, key, default=None):
        idx = self._map.get(key)
        if idx is None:
            return default
        return self._values[idx]

    def __iter__(self):
        return iter(self._values)

    def __contains__(self, key):
        return key in self._map


class SqliteCursor:
    """Thin wrapper producing ``SqliteRow`` results from a SQLAlchemy ``CursorResult``."""

    def __init__(self, result):
        self._result = result

    def fetchone(self):
        row = self._result.fetchone()
        if row is None:
            return None
        if isinstance(row, SqliteRow):
            return row
        return SqliteRow(list(self._result.keys()), row)

    def fetchall(self):
        rows = self._result.fetchall()
        if not rows:
            return []
        if isinstance(rows[0], SqliteRow):
            return rows
        keys = list(self._result.keys())
        return [SqliteRow(keys, r) for r in rows]

    @property
    def lastrowid(self):
        return getattr(self._result, "lastrowid", None)

    @property
    def rowcount(self):
        return self._result.rowcount

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Qmark -> dialect placeholder translation
# ---------------------------------------------------------------------------
def _compile_qmark(sql: str, dialect: str) -> str:
    """Translate legacy ``?`` placeholders to the bound dialect's style."""
    if dialect == "postgresql":
        return re.sub(r"\?", "%s", sql)
    return sql


def _coerce_params(params):
    out = []
    for p in params:
        if isinstance(p, bool):
            out.append(int(p))
        else:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Schema-drift resolver: drop columns that don't exist in the ORM schema
# ---------------------------------------------------------------------------
_COLUMN_TOKEN = "[A-Za-z_][A-Za-z0-9_]*"
_INSERT_RE = re.compile(
    r"^\s*INSERT\s+INTO\s+(" + _COLUMN_TOKEN + r")\s*\((.*?)\)\s*VALUES\s*\((.*)\)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_LIMIT_NULL_RE = re.compile(r"\s*LIMIT\s+NULL", re.IGNORECASE)


def _split_values(body: str) -> list[str]:
    """Split a VALUES body on top-level commas, respecting quoted literals."""
    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for ch in body:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            current.append(ch)
        elif ch == ",":
            tokens.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        tokens.append(tail)
    return tokens


class _TableInspector:
    """Cached column-name lookup from ORM metadata (no DB round-trip).

    Using a separate engine-level ``Connection`` for reflection is unsafe here: closing that
    connection implicitly ROLLS BACK the pooled physical connection, silently discarding any
    uncommitted legacy writes on the same engine. So column sets come from ``Base.metadata``
    first; reflective lookup (only reached when a table is not in the ORM metadata) runs
    through the session's own live connection.
    """

    def __init__(self):
        self._cache: dict[str, frozenset[str]] = {}

    def columns_for(self, session: Session, table: str) -> frozenset[str] | None:
        key = table.lower()
        if key in self._cache:
            return self._cache[key]

        table_obj = _table_metadata(session, table)
        if table_obj is not None:
            result = frozenset(c.name.lower() for c in table_obj.columns)
            self._cache[key] = result
            return result

        result = None
        try:
            insp = sa_inspect(session.connection())
            if insp.has_table(key):
                cols = {c["name"].lower() for c in insp.get_columns(key)}
                result = frozenset(cols)
        except Exception:
            result = None
        self._cache[key] = result
        return result


_TABLE_INSPECTOR = _TableInspector()


def _fill_insert_defaults(table_obj, out_cols, out_vals, out_params):
    """Append NOT NULL columns that the INSERT omits, using the ORM's client defaults.

    The legacy Phase-2 schema declared server-side ``DEFAULT`` clauses that the ORM
    ``create_all`` DDL does not emit (the ORM columns model them as client-side ``default``).
    To keep legacy INSERT statements working on both schema shapes, omitted required columns
    are appended as bound parameters evaluated the same way the ORM would evaluate them.
    ``out_params`` is mutated in place; returns the (cols, vals) lists to append.
    """
    kept_lower = {c.lower() for c in out_cols}
    fill_cols: list[str] = []
    fill_vals: list[str] = []
    for col in table_obj.columns.values():
        name = col.name.lower()
        if name in kept_lower:
            continue
        if col.nullable:
            continue
        if col.server_default is not None:
            continue  # the database supplies it
        default = col.default
        if default is None:
            continue
        from sqlalchemy.sql.schema import ColumnDefault

        if isinstance(default, ColumnDefault):
            # SQLAlchemy wraps client defaults in ColumnDefault; materialise the arg
            default = default.arg
        if isinstance(default, (int, float, str, bool)):
            value = default
        elif callable(default):
            # SQLAlchemy 2.0 default callables take a (context) argument
            try:
                value = default(None)
            except TypeError:
                value = default()
        else:
            continue
        fill_cols.append(col.name)
        fill_vals.append("?")
        out_params.append(value)
    return fill_cols, fill_vals


def _resolve_insert_drift(sql, params, table_obj, real_cols):
    """Rewrite ``INSERT INTO t (c1, c2, ...) VALUES (v1, v2, ...)``:
    drop listed columns that don't exist in the target table, and append omitted required
    columns (with their defaults). Both rewrites preserve positional param/values alignment.

    Returns ``(sql, params)`` unchanged when there is nothing to fix.
    """
    m = _INSERT_RE.match(sql)
    if not m:
        return sql, params
    table, cols_body, vals_body = m.group(1), m.group(2), m.group(3)
    col_tokens = _split_values(cols_body)
    val_tokens = _split_values(vals_body)
    if not col_tokens or len(col_tokens) != len(val_tokens):
        return sql, params

    out_cols: list[str] = []
    out_vals: list[str] = []
    out_params: list[Any] = []
    dropped = False
    q_index = 0
    for col, val in zip(col_tokens, val_tokens):
        param = None
        if val == "?":
            param = params[q_index] if q_index < len(params) else None
            q_index += 1
        if real_cols is not None and col.lower() not in real_cols:
            dropped = True
            continue
        out_cols.append(col)
        out_vals.append(val)
        if val == "?":
            out_params.append(param)

    fill_cols: list[str] = []
    fill_vals: list[str] = []
    if table_obj is not None:
        fill_cols, fill_vals = _fill_insert_defaults(
            table_obj, out_cols, out_vals, out_params
        )
    if not dropped and not fill_cols:
        return sql, params

    new_sql = "INSERT INTO {} ({}) VALUES ({})".format(
        table, ", ".join(out_cols + fill_cols), ", ".join(out_vals + fill_vals)
    )
    return new_sql, out_params


_METADATA_TABLES: dict[str, Any] = {}


def _table_metadata(session: Session, table: str):
    """Return the ORM ``Table`` for *table* (for client-default lookup), or None."""
    if table.lower() in _METADATA_TABLES:
        return _METADATA_TABLES[table.lower()]
    result = None
    try:
        from db.models import Base
        result = Base.metadata.tables.get(table.lower())
    except Exception:
        result = None
    _METADATA_TABLES[table.lower()] = result
    return result


# ---------------------------------------------------------------------------
# SqliteConn
# ---------------------------------------------------------------------------
class SqliteConn:
    """Presents a raw-sqlite3-shaped connection over an ORM ``Session``.

    Intended consumer: Phase-2 modules written against ``sqlite3``. Backed by the real shared
    transaction, so legacy writes and ORM writes are atomic together.
    """

    def __init__(self, session: Session):
        self._session = session

    # -- sqlite3-compatible surface ------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] | dict | None = None) -> SqliteCursor:
        if params is None:
            params = ()
        if isinstance(params, dict):
            # Legacy modules never used named params; normalize defensively.
            params = tuple(params.values())
        else:
            params = list(params)

        self._session.flush()

        sql, params = self._resolve(sql, params)
        dialect = self._dialect_name()
        compiled = _compile_qmark(sql, dialect)
        params = _coerce_params(params)

        try:
            result = self._session.connection().exec_driver_sql(compiled, tuple(params))
        except Exception as exc:
            raise RuntimeError(
                f"Legacy SQL failed on dialect={dialect}: {exc}; sql={compiled[:240]}"
            ) from exc
        return SqliteCursor(result)

    def commit(self) -> None:
        self._session.commit()

    def close(self) -> None:
        pass

    # -- internals -------------------------------------------------------------
    def _dialect_name(self) -> str:
        bind = self._session.get_bind()
        dialect = getattr(bind, "dialect", None)
        return dialect.name if dialect is not None else "sqlite"

    def _resolve(self, sql: str, params: list[Any]):
        m = _INSERT_RE.match(sql)
        if m is not None:
            real_cols = _TABLE_INSPECTOR.columns_for(self._session, m.group(1))
            table_obj = _table_metadata(self._session, m.group(1))
            sql, params = _resolve_insert_drift(sql, params, table_obj, real_cols)
        sql = _LIMIT_NULL_RE.sub("", sql)
        return sql, params