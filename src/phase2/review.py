"""Human review workflow. CLI-based for Phase 2 (no auth system - reviewer is a plain string
identifier, e.g. an OS username or a --reviewer flag value, per task spec).

Every action writes a requirement_review row with old_state/new_state snapshots, so a
human-edited requirement never silently overwrites the original LLM extraction - the original is
always recoverable from requirement_versions / requirement_review history.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _snapshot(conn, requirement_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM requirements WHERE requirement_id = ?", (requirement_id,)).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def list_for_review(conn, status: str = "NEEDS_REVIEW", limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM requirements WHERE status = ? ORDER BY updated_at LIMIT ?",
        (status, limit),
    ).fetchall()
    out = []
    for r in rows:
        evidence = conn.execute(
            "SELECT * FROM requirement_evidence WHERE requirement_id = ?", (r["requirement_id"],)
        ).fetchall()
        out.append({
            "requirement_id": r["requirement_id"],
            "category": r["category"],
            "name": r["name"],
            "field": r["field"],
            "operator": r["operator"],
            "value_json": r["value_json"],
            "unit": r["unit"],
            "mandatory": r["mandatory"],
            "status": r["status"],
            "confidence": r["confidence"],
            "evidence": [{"chunk_id": e["chunk_id"], "text": e["evidence_text"]} for e in evidence],
        })
    return out


def approve(conn, requirement_id: str, reviewer: str, notes: str | None = None) -> None:
    old_state = _snapshot(conn, requirement_id)
    if old_state is None:
        raise ValueError(f"unknown requirement_id {requirement_id}")
    now = _now_iso()
    conn.execute(
        "UPDATE requirements SET status = 'APPROVED', updated_at = ? WHERE requirement_id = ?",
        (now, requirement_id),
    )
    new_state = _snapshot(conn, requirement_id)
    conn.execute(
        """
        INSERT INTO requirement_review (requirement_id, reviewer, action, old_state_json,
                                          new_state_json, notes, created_at)
        VALUES (?, ?, 'APPROVE', ?, ?, ?, ?)
        """,
        (requirement_id, reviewer, json.dumps(old_state, default=str), json.dumps(new_state, default=str),
         notes, now),
    )
    conn.commit()


def reject(conn, requirement_id: str, reviewer: str, notes: str | None = None) -> None:
    old_state = _snapshot(conn, requirement_id)
    if old_state is None:
        raise ValueError(f"unknown requirement_id {requirement_id}")
    now = _now_iso()
    conn.execute(
        "UPDATE requirements SET status = 'REJECTED', updated_at = ? WHERE requirement_id = ?",
        (now, requirement_id),
    )
    new_state = _snapshot(conn, requirement_id)
    conn.execute(
        """
        INSERT INTO requirement_review (requirement_id, reviewer, action, old_state_json,
                                          new_state_json, notes, created_at)
        VALUES (?, ?, 'REJECT', ?, ?, ?, ?)
        """,
        (requirement_id, reviewer, json.dumps(old_state, default=str), json.dumps(new_state, default=str),
         notes, now),
    )
    conn.commit()


def mark_needs_review(conn, requirement_id: str, reviewer: str, notes: str | None = None) -> None:
    old_state = _snapshot(conn, requirement_id)
    if old_state is None:
        raise ValueError(f"unknown requirement_id {requirement_id}")
    now = _now_iso()
    conn.execute(
        "UPDATE requirements SET status = 'NEEDS_REVIEW', updated_at = ? WHERE requirement_id = ?",
        (now, requirement_id),
    )
    new_state = _snapshot(conn, requirement_id)
    conn.execute(
        """
        INSERT INTO requirement_review (requirement_id, reviewer, action, old_state_json,
                                          new_state_json, notes, created_at)
        VALUES (?, ?, 'MARK_NEEDS_REVIEW', ?, ?, ?, ?)
        """,
        (requirement_id, reviewer, json.dumps(old_state, default=str), json.dumps(new_state, default=str),
         notes, now),
    )
    conn.commit()


def edit_field(conn, requirement_id: str, reviewer: str, field_name: str, new_value,
                notes: str | None = None) -> None:
    """Edit one structured field (e.g. 'value_json', 'mandatory', 'category'). Bumps
    current_version and writes both a requirement_versions row and a requirement_review row, so
    the pre-edit LLM extraction is never lost.
    """
    allowed_fields = {
        "category", "name", "description", "field", "operator", "value_json", "unit",
        "mandatory", "condition_json", "exception_json", "applies_to", "criticality", "status",
    }
    if field_name not in allowed_fields:
        raise ValueError(f"field '{field_name}' is not editable; allowed: {sorted(allowed_fields)}")

    old_state = _snapshot(conn, requirement_id)
    if old_state is None:
        raise ValueError(f"unknown requirement_id {requirement_id}")
    now = _now_iso()

    conn.execute(
        f"UPDATE requirements SET {field_name} = ?, current_version = current_version + 1, updated_at = ? "
        f"WHERE requirement_id = ?",
        (new_value, now, requirement_id),
    )
    new_state = _snapshot(conn, requirement_id)

    next_version = (old_state["current_version"] or 1) + 1
    conn.execute(
        """
        INSERT INTO requirement_versions (requirement_id, version_number, snapshot_json,
                                            document_version, change_type, created_at, created_by)
        VALUES (?, ?, ?, ?, 'HUMAN_EDIT', ?, ?)
        """,
        (requirement_id, next_version, json.dumps(new_state, default=str),
         0,  # document_version unknown/not-applicable for a manual edit
         now, reviewer),
    )
    conn.execute(
        """
        INSERT INTO requirement_review (requirement_id, reviewer, action, old_state_json,
                                          new_state_json, notes, created_at)
        VALUES (?, ?, 'EDIT', ?, ?, ?, ?)
        """,
        (requirement_id, reviewer, json.dumps(old_state, default=str), json.dumps(new_state, default=str),
         notes, now),
    )
    conn.commit()
