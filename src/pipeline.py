"""Phase-1 pipeline orchestrator.

For every ACTIVE row in government_sources:
    fetch -> store raw snapshot -> extract structured text -> hash -> classify change
    -> persist government_documents / document_versions / document_chunks / fetch_log

Usage:
    python src/pipeline.py                 # run against all active sources (network required)
    python src/pipeline.py --source-id X    # run a single source
    python src/pipeline.py --offline-fixture path/to/file.html --source-id X
                                             # run one source against a local fixture instead of the network
                                             # (used by tests/test_pipeline_offline.py)
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from change_detector import ChangeType, classify_change
from db import ROOT, get_connection, init_schema
from extractor_html import extract_html
from extractor_pdf import extract_pdf
from fetcher import FetchResult, FetchStatus, fetch
from hashing import sha256_bytes, sha256_text

# fetch statuses that should also demote the source's own health flag in government_sources.status
_SOURCE_STATUS_FOR_FETCH_STATUS = {
    FetchStatus.ACCESS_BLOCKED: "ACCESS_BLOCKED",
    FetchStatus.NETWORK_RESTRICTED: "NETWORK_RESTRICTED",
    FetchStatus.REQUIRES_BROWSER: "REQUIRES_BROWSER",
    FetchStatus.REQUIRES_MANUAL_DOWNLOAD: "REQUIRES_MANUAL_DOWNLOAD",
    FetchStatus.FETCH_FAILED: "BROKEN",
    FetchStatus.INVALID_CONTENT: "BROKEN",
}

RAW_ROOT = ROOT / "raw"
EXTRACTED_ROOT = ROOT / "extracted"
VALIDATION_ROOT = ROOT / "validation"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _snapshot_dir(country_id: str, visa_category: str, source_id: str) -> Path:
    # raw/AU/skilled/SRC-AU-189-MAIN/2026-08-20/
    category_folder = visa_category.lower() if visa_category != "LEGISLATION" else "legislation"
    d = RAW_ROOT / country_id / category_folder / source_id / _today()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _extracted_dir(country_id: str, visa_category: str, source_id: str) -> Path:
    category_folder = visa_category.lower() if visa_category != "LEGISLATION" else "legislation"
    d = EXTRACTED_ROOT / country_id / category_folder / source_id / _today()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _raw_filename(source_type: str) -> str:
    return "content.html" if source_type == "HTML" else "content.pdf"


def process_source(conn, source_row, offline_fixture: Path | None = None) -> dict:
    """Run the full pipeline for one government_sources row. Returns a run-report dict."""
    source_id = source_row["source_id"]
    url = source_row["url"]
    source_type = source_row["source_type"]
    country_id = source_row["country_id"]
    program_id = source_row["program_id"]
    visa_category = source_row["visa_category"]
    previous_hash = source_row["last_hash"]

    report: dict = {"source_id": source_id, "url": url}

    # --- 1. FETCH -----------------------------------------------------------------
    if offline_fixture is not None:
        raw_bytes = offline_fixture.read_bytes()
        fetch_result = FetchResult(
            url=url, status=FetchStatus.OK, http_status=200,
            content_type="text/html" if source_type == "HTML" else "application/pdf",
            raw_bytes=raw_bytes, duration_ms=0, error=None,
        )
    else:
        fetch_result = fetch(url, source_type=source_type)

    fetched_at = _now_iso()

    if not fetch_result.ok or fetch_result.raw_bytes is None:
        conn.execute(
            """
            INSERT INTO fetch_log (source_id, fetched_at, http_status, status, content_hash, changed, duration_ms, error)
            VALUES (?, ?, ?, ?, NULL, 0, ?, ?)
            """,
            (source_id, fetched_at, fetch_result.http_status, fetch_result.status.value,
             fetch_result.duration_ms, fetch_result.error),
        )
        source_status = _SOURCE_STATUS_FOR_FETCH_STATUS.get(fetch_result.status, "BROKEN")
        conn.execute(
            "UPDATE government_sources SET last_checked_at = ?, status = ? WHERE source_id = ?",
            (fetched_at, source_status, source_id),
        )
        conn.commit()
        report.update({
            "status": fetch_result.status.value,
            "error": fetch_result.error,
            "http_status": fetch_result.http_status,
        })
        return report

    # --- 2. RAW STORAGE (immutable snapshot) ---------------------------------------
    snap_dir = _snapshot_dir(country_id, visa_category, source_id)
    raw_path = snap_dir / _raw_filename(source_type)
    raw_path.write_bytes(fetch_result.raw_bytes)

    raw_hash = sha256_bytes(fetch_result.raw_bytes)
    change_type = classify_change(previous_hash, raw_hash)

    # --- 3. EXTRACTION (only if new or changed - saves work on UNCHANGED) ----------
    extracted_path = None
    title = None
    chunk_objs = []
    needs_ocr = False

    if change_type != ChangeType.UNCHANGED:
        try:
            if source_type == "HTML":
                result = extract_html(fetch_result.raw_bytes, url)
                title = result.title
                chunk_objs = result.chunks
                clean_text = result.clean_text
            else:  # PDF
                pdf_result = extract_pdf(fetch_result.raw_bytes)
                title = pdf_result.title
                chunk_objs = pdf_result.chunks
                clean_text = pdf_result.clean_text
                needs_ocr = pdf_result.needs_ocr
        except Exception as e:
            conn.execute(
                """
                INSERT INTO fetch_log (source_id, fetched_at, http_status, status, content_hash, changed, duration_ms, error)
                VALUES (?, ?, ?, 'EXTRACTION_FAILED', ?, 0, ?, ?)
                """,
                (source_id, fetched_at, fetch_result.http_status, raw_hash, fetch_result.duration_ms, str(e)),
            )
            conn.commit()
            report.update({"status": "EXTRACTION_FAILED", "error": str(e), "http_status": fetch_result.http_status})
            return report

        extracted_dir = _extracted_dir(country_id, visa_category, source_id)
        extracted_path = extracted_dir / "extracted.json"
        extracted_path.write_text(
            json.dumps(
                {
                    "source_id": source_id,
                    "url": url,
                    "title": title,
                    "extracted_at": fetched_at,
                    "content_hash": raw_hash,
                    "clean_text_hash": sha256_text(clean_text),
                    "needs_ocr": needs_ocr,
                    "chunks": [
                        {
                            "chunk_type": c.chunk_type,
                            "heading_path": c.heading_path,
                            "order_index": c.order_index,
                            "raw_text": c.raw_text,
                            "page_number": getattr(c, "page_number", None),
                        }
                        for c in chunk_objs
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # --- 4. PERSIST TO DB -----------------------------------------------------------
    document_id = f"DOC-{source_id.replace('SRC-', '')}"

    existing_doc = conn.execute(
        "SELECT * FROM government_documents WHERE document_id = ?", (document_id,)
    ).fetchone()

    if change_type == ChangeType.INITIAL:
        version_number = 1
        conn.execute(
            """
            INSERT INTO government_documents
                (document_id, source_id, country_id, program_id, visa_category, document_type,
                 url, title, retrieved_at, content_hash, current_version, language,
                 raw_path, extracted_path, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'en', ?, ?, ?)
            """,
            (
                document_id, source_id, country_id, program_id, visa_category,
                source_row["source_subtype"] or "GENERAL",
                url, title, fetched_at, raw_hash, version_number,
                str(raw_path), str(extracted_path) if extracted_path else None,
                "NEEDS_OCR" if needs_ocr else "CURRENT",
            ),
        )
    elif change_type == ChangeType.CHANGED:
        version_number = (existing_doc["current_version"] if existing_doc else 0) + 1
        conn.execute(
            """
            UPDATE government_documents
            SET title = ?, retrieved_at = ?, content_hash = ?, current_version = ?,
                raw_path = ?, extracted_path = ?, status = ?
            WHERE document_id = ?
            """,
            (
                title, fetched_at, raw_hash, version_number,
                str(raw_path), str(extracted_path) if extracted_path else None,
                "NEEDS_OCR" if needs_ocr else "CURRENT",
                document_id,
            ),
        )
    else:  # UNCHANGED
        version_number = existing_doc["current_version"] if existing_doc else 1
        conn.execute(
            "UPDATE government_documents SET retrieved_at = ? WHERE document_id = ?",
            (fetched_at, document_id),
        )

    if change_type in (ChangeType.INITIAL, ChangeType.CHANGED):
        cur = conn.execute(
            """
            INSERT INTO document_versions
                (document_id, version_number, content_hash, retrieved_at, raw_path, extracted_path, change_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id, version_number, raw_hash, fetched_at,
                str(raw_path), str(extracted_path) if extracted_path else None,
                change_type.value,
            ),
        )
        version_id = cur.lastrowid

        for c in chunk_objs:
            conn.execute(
                """
                INSERT INTO document_chunks
                    (document_id, version_id, chunk_type, heading_path, order_index, raw_text, page_number)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id, version_id, c.chunk_type, c.heading_path,
                    c.order_index, c.raw_text, getattr(c, "page_number", None),
                ),
            )

    conn.execute(
        "UPDATE government_sources SET last_checked_at = ?, last_hash = ?, status = 'ACTIVE' WHERE source_id = ?",
        (fetched_at, raw_hash, source_id),
    )

    conn.execute(
        """
        INSERT INTO fetch_log (source_id, fetched_at, http_status, status, content_hash, changed, duration_ms, error)
        VALUES (?, ?, ?, 'OK', ?, ?, ?, NULL)
        """,
        (
            source_id, fetched_at, fetch_result.http_status, raw_hash,
            1 if change_type != ChangeType.UNCHANGED else 0, fetch_result.duration_ms,
        ),
    )

    conn.commit()

    report.update(
        {
            "status": "OK",
            "http_status": fetch_result.http_status,
            "change_type": change_type.value,
            "content_hash": raw_hash,
            "document_id": document_id,
            "version": version_number,
            "chunk_count": len(chunk_objs),
            "needs_ocr": needs_ocr,
            "raw_path": str(raw_path),
            "extracted_path": str(extracted_path) if extracted_path else None,
        }
    )
    return report


def run_pipeline(source_id_filter: str | None = None, offline_fixture: Path | None = None) -> dict:
    conn = init_schema()
    query = "SELECT * FROM government_sources WHERE status != 'INACTIVE'"
    params: tuple = ()
    if source_id_filter:
        query += " AND source_id = ?"
        params = (source_id_filter,)
    sources = conn.execute(query, params).fetchall()

    results = []
    for src in sources:
        print(f"[pipeline] processing {src['source_id']} ...")
        r = process_source(conn, src, offline_fixture=offline_fixture)
        detail = r.get('change_type') or r.get('error') or ''
        print(f"           -> {r.get('status')} ({detail})")
        results.append(r)

    failure_statuses = {
        "FETCH_FAILED", "ACCESS_BLOCKED", "NETWORK_RESTRICTED",
        "REQUIRES_BROWSER", "REQUIRES_MANUAL_DOWNLOAD", "INVALID_CONTENT", "EXTRACTION_FAILED",
    }
    failures = [
        {"source_id": r["source_id"], "status": r.get("http_status"), "reason": r.get("status"),
         "error": r.get("error")}
        for r in results if r.get("status") in failure_statuses
    ]
    run_report = {
        "run_at": _now_iso(),
        "total_sources": len(results),
        "successful": sum(1 for r in results if r.get("status") == "OK"),
        "new": sum(1 for r in results if r.get("change_type") == "INITIAL"),
        "changed": sum(1 for r in results if r.get("change_type") == "CHANGED"),
        "unchanged": sum(1 for r in results if r.get("change_type") == "UNCHANGED"),
        "failed": len(failures),
        "failures": failures,
        "results": results,
    }

    VALIDATION_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = VALIDATION_ROOT / "run_report.json"
    report_path.write_text(json.dumps(run_report, indent=2), encoding="utf-8")
    print(f"[pipeline] run report written to {report_path}")

    conn.close()
    return run_report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Phase-1 government source pipeline.")
    parser.add_argument("--source-id", help="Only process this source_id.")
    parser.add_argument("--offline-fixture", help="Path to a local HTML/PDF file to use instead of a live fetch.")
    args = parser.parse_args()

    fixture = Path(args.offline_fixture) if args.offline_fixture else None
    run_pipeline(source_id_filter=args.source_id, offline_fixture=fixture)
