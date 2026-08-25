"""Phase 2 CLI entry point.

Usage:
    python src/phase2/run_extraction.py --document-id DOC-AU-189-MAIN
    python src/phase2/run_extraction.py --source-id SRC-AU-189-MAIN
    python src/phase2/run_extraction.py --document-id DOC-AU-189-MAIN --dry-run
    python src/phase2/run_extraction.py --document-id DOC-AU-189-MAIN --model mock-llm-v1 --limit 5
    python src/phase2/run_extraction.py --review --status NEEDS_REVIEW
    python src/phase2/run_extraction.py --review --approve REQ-SKILLED_189-abcd1234 --reviewer alice
    python src/phase2/run_extraction.py --review --reject REQ-SKILLED_189-abcd1234 --reviewer alice --notes "..."

Without --model, defaults to 'mock-llm-v1' (no network/API key required). Pass
--model claude-sonnet-4-6 (with ANTHROPIC_API_KEY set) to use the real extractor.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import ROOT, init_schema  # noqa: E402
from llm_client import PROMPT_VERSION, get_client  # noqa: E402
from requirement_extractor import extract_from_document  # noqa: E402
from requirement_repository import complete_extraction_run, create_extraction_run, persist_requirement  # noqa: E402
import review as review_mod  # noqa: E402

VALIDATION_ROOT = ROOT / "validation"


def _load_chunks_for_document(conn, document_id: str) -> tuple[dict | None, int, list[dict]]:
    doc = conn.execute("SELECT * FROM government_documents WHERE document_id = ?", (document_id,)).fetchone()
    if doc is None:
        return None, 0, []
    version_row = conn.execute(
        "SELECT version_id FROM document_versions WHERE document_id = ? AND version_number = ?",
        (document_id, doc["current_version"]),
    ).fetchone()
    if version_row is None:
        return dict(doc), doc["current_version"], []
    chunk_rows = conn.execute(
        "SELECT * FROM document_chunks WHERE document_id = ? AND version_id = ? ORDER BY order_index",
        (document_id, version_row["version_id"]),
    ).fetchall()
    chunks = [dict(c) for c in chunk_rows]
    return dict(doc), doc["current_version"], chunks


def run_for_document(conn, document_id: str, model: str, dry_run: bool, limit: int | None,
                       context_window: int = 1) -> dict:
    doc, version, chunks = _load_chunks_for_document(conn, document_id)
    if doc is None:
        return {"document_id": document_id, "status": "DOCUMENT_NOT_FOUND"}
    if not chunks:
        return {"document_id": document_id, "status": "NO_CHUNKS", "note": "run Phase 1 pipeline first"}

    source_row = conn.execute(
        "SELECT * FROM government_sources WHERE source_id = ?", (doc["source_id"],)
    ).fetchone()

    run_id = create_extraction_run(conn, document_id, version, model, PROMPT_VERSION, dry_run)

    client = get_client(model)
    source_ctx = {"source_id": source_row["source_id"], "authority": source_row["authority"],
                  "program_id": doc["program_id"]}
    document_ctx = {"document_id": document_id, "version": version, "title": doc["title"]}

    outcomes = extract_from_document(
        source=source_ctx, document=document_ctx, all_chunks=chunks, llm_client=client,
        context_window=context_window,
    )
    if limit:
        outcomes = outcomes[:limit]

    counters = {
        "chunks_seen": len(chunks), "candidate_chunks": len(outcomes), "llm_calls": len(outcomes),
        "requirements_extracted": 0, "validated": 0, "needs_review": 0, "rejected": 0,
        "evidence_failures": 0, "schema_failures": 0,
    }
    persisted = []

    for outcome in outcomes:
        if outcome.schema_errors:
            counters["schema_failures"] += 1
            continue
        for req in outcome.accepted_requirements:
            counters["requirements_extracted"] += 1
            counters["validated"] += 1
            result = persist_requirement(
                conn, program_id=doc["program_id"], country_id=doc["country_id"], requirement=req,
                document_id=document_id, document_version=version, extraction_run_id=run_id,
                dry_run=dry_run,
            )
            persisted.append(result)
        for item in outcome.needs_review_requirements:
            counters["requirements_extracted"] += 1
            counters["needs_review"] += 1
            req = dict(item["requirement"])
            result = persist_requirement(
                conn, program_id=doc["program_id"], country_id=doc["country_id"], requirement=req,
                document_id=document_id, document_version=version, extraction_run_id=run_id,
                dry_run=dry_run,
            )
            if not dry_run and result.get("requirement_id") and result["action"] == "CREATED":
                conn.execute(
                    "UPDATE requirements SET status = 'NEEDS_REVIEW' WHERE requirement_id = ?",
                    (result["requirement_id"],),
                )
                conn.commit()
            persisted.append({**result, "reasons": item["reasons"]})
        for item in outcome.rejected_requirements:
            counters["rejected"] += 1
            if any("evidence" in r.lower() or "fabricat" in r.lower() for r in item["reasons"]):
                counters["evidence_failures"] += 1

    complete_extraction_run(conn, run_id, counters, status="DRY_RUN" if dry_run else "COMPLETED")

    return {
        "document_id": document_id, "document_version": version, "run_id": run_id,
        "model": model, "dry_run": dry_run, "status": "COMPLETED", **counters,
        "persisted": persisted,
    }


def run_for_source(conn, source_id: str, **kwargs) -> dict:
    doc_row = conn.execute(
        "SELECT document_id FROM government_documents WHERE source_id = ?", (source_id,)
    ).fetchone()
    if doc_row is None:
        return {"source_id": source_id, "status": "NO_DOCUMENT",
                "note": "no government_documents row for this source - run Phase 1 pipeline first"}
    return run_for_document(conn, doc_row["document_id"], **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2 requirement extraction CLI.")
    parser.add_argument("--document-id")
    parser.add_argument("--source-id")
    parser.add_argument("--model", default="mock-llm-v1")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--context-window", type=int, default=1)

    parser.add_argument("--review", action="store_true", help="Enter review mode instead of extraction.")
    parser.add_argument("--status", default="NEEDS_REVIEW", help="Review mode: list requirements with this status.")
    parser.add_argument("--approve")
    parser.add_argument("--reject")
    parser.add_argument("--mark-needs-review", dest="mark_needs_review")
    parser.add_argument("--reviewer", default="cli-user")
    parser.add_argument("--notes")

    args = parser.parse_args()
    conn = init_schema()

    if args.review:
        if args.approve:
            review_mod.approve(conn, args.approve, args.reviewer, args.notes)
            print(f"APPROVED {args.approve} by {args.reviewer}")
        elif args.reject:
            review_mod.reject(conn, args.reject, args.reviewer, args.notes)
            print(f"REJECTED {args.reject} by {args.reviewer}")
        elif args.mark_needs_review:
            review_mod.mark_needs_review(conn, args.mark_needs_review, args.reviewer, args.notes)
            print(f"MARKED NEEDS_REVIEW {args.mark_needs_review} by {args.reviewer}")
        else:
            items = review_mod.list_for_review(conn, status=args.status)
            print(json.dumps(items, indent=2, default=str))
        conn.close()
        return

    if not args.document_id and not args.source_id:
        parser.error("provide --document-id or --source-id (or --review)")

    kwargs = dict(model=args.model, dry_run=args.dry_run, limit=args.limit,
                   context_window=args.context_window)
    if args.document_id:
        report = run_for_document(conn, args.document_id, **kwargs)
    else:
        report = run_for_source(conn, args.source_id, **kwargs)

    VALIDATION_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = VALIDATION_ROOT / "phase2_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "persisted"}, indent=2, default=str))
    print(f"[phase2] report written to {report_path}")
    conn.close()


if __name__ == "__main__":
    main()
