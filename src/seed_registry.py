"""Load sources/source_registry.seed.json into countries / immigration_programs / government_sources.

Idempotent: re-running upserts rows by primary key rather than duplicating them.
"""
from __future__ import annotations

import json
from pathlib import Path

from db import ROOT, get_connection, init_schema

SEED_PATH = ROOT / "sources" / "source_registry.seed.json"


def load_seed(path: Path = SEED_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def upsert_countries(conn, countries: list[dict]) -> int:
    n = 0
    for c in countries:
        conn.execute(
            """
            INSERT INTO countries (country_id, name, iso_code)
            VALUES (:country_id, :name, :iso_code)
            ON CONFLICT(country_id) DO UPDATE SET
                name = excluded.name,
                iso_code = excluded.iso_code;
            """,
            c,
        )
        n += 1
    return n


def upsert_programs(conn, programs: list[dict]) -> int:
    n = 0
    for p in programs:
        conn.execute(
            """
            INSERT INTO immigration_programs
                (program_id, country_id, visa_category, subclass_code, name, status)
            VALUES
                (:program_id, :country_id, :visa_category, :subclass_code, :name, COALESCE(:status,'ACTIVE'))
            ON CONFLICT(program_id) DO UPDATE SET
                country_id = excluded.country_id,
                visa_category = excluded.visa_category,
                subclass_code = excluded.subclass_code,
                name = excluded.name,
                status = excluded.status;
            """,
            {**p, "status": p.get("status", "ACTIVE")},
        )
        n += 1
    return n


def upsert_sources(conn, sources: list[dict]) -> int:
    n = 0
    for s in sources:
        row = {
            "source_id": s["source_id"],
            "country_id": s["country_id"],
            "program_id": s["program_id"],
            "visa_category": s["visa_category"],
            "authority": s["authority"],
            "url": s["url"],
            "source_type": s["source_type"],
            "source_subtype": s.get("source_subtype"),
            "priority": s["priority"],
            "monitoring_method": s.get("monitoring_method", "WEB"),
            "crawl_frequency": s.get("crawl_frequency", "DAILY"),
            "notes": s.get("notes"),
        }
        conn.execute(
            """
            INSERT INTO government_sources
                (source_id, country_id, program_id, visa_category, authority, url,
                 source_type, source_subtype, priority, monitoring_method, crawl_frequency,
                 status, notes)
            VALUES
                (:source_id, :country_id, :program_id, :visa_category, :authority, :url,
                 :source_type, :source_subtype, :priority, :monitoring_method, :crawl_frequency,
                 'ACTIVE', :notes)
            ON CONFLICT(source_id) DO UPDATE SET
                program_id = excluded.program_id,
                visa_category = excluded.visa_category,
                authority = excluded.authority,
                url = excluded.url,
                source_type = excluded.source_type,
                source_subtype = excluded.source_subtype,
                priority = excluded.priority,
                monitoring_method = excluded.monitoring_method,
                crawl_frequency = excluded.crawl_frequency,
                notes = excluded.notes;
            """,
            row,
        )
        n += 1
    return n


def main() -> None:
    conn = init_schema()
    data = load_seed()
    n_countries = upsert_countries(conn, data["countries"])
    n_programs = upsert_programs(conn, data["immigration_programs"])
    n_sources = upsert_sources(conn, data["government_sources"])
    conn.commit()
    print(f"Seeded: {n_countries} countries, {n_programs} programs, {n_sources} sources.")
    conn.close()


if __name__ == "__main__":
    main()
