"""Tests for environment-driven LLM configuration wiring.

Covers:
* EXTRACTION_MODEL selects the requirement-extraction model (default stays
  mock-llm-v1, and a real prefix routes to the right provider client class.
* LLM_DAILY_BUDGET_USD / LLM_MONTHLY_BUDGET_USD are read into the discovery
  BudgetTracker, with the module fallback preserved when unset (including 0.0
  as a legitimate "block all spend" value).
* The seed registry exposes WORK/STUDENT/VISITOR immigration_program rows so
  discovered sources in those categories have a program anchor to attach to.
All tests are offline: no real network or billed LLM call is made.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from source_discovery import _discovery_budget  # noqa: E402
from source_discovery import _resolve_category  # noqa: E402
from utils.fetch_document import _extraction_model  # noqa: E402


# ---------------------------------------------------------------------------
# EXTRACTION_MODEL wiring
# ---------------------------------------------------------------------------
class TestExtractionModelEnv:
    def test_defaults_to_mock_when_unset(self, monkeypatch):
        monkeypatch.delenv("EXTRACTION_MODEL", raising=False)
        assert _extraction_model() == "mock-llm-v1"

    def test_reads_env_when_set(self, monkeypatch):
        monkeypatch.setenv("EXTRACTION_MODEL", "gemini-flash-lite-latest")
        assert _extraction_model() == "gemini-flash-lite-latest"

    def test_model_routes_to_provider_client(self, monkeypatch):
        monkeypatch.setenv("EXTRACTION_MODEL", "gemini-flash-lite-latest")
        monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-for-dispatch-test")
        from phase2.llm_client import get_client
        client = get_client(_extraction_model(), visa_category="WORK")
        from phase2.llm_client import GeminiLLMClient, MockLLMClient
        assert isinstance(client, GeminiLLMClient)
        assert not isinstance(client, MockLLMClient)


# ---------------------------------------------------------------------------
# Discovery budget wiring
# ---------------------------------------------------------------------------
class TestDiscoveryBudgetEnv:
    def test_uses_env_values(self, monkeypatch):
        monkeypatch.setenv("LLM_DAILY_BUDGET_USD", "2.50")
        monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "75.0")
        bt = _discovery_budget()
        assert bt.daily_limit == 2.5
        assert bt.monthly_limit == 75.0

    def test_falls_back_when_unset(self, monkeypatch):
        monkeypatch.delenv("LLM_DAILY_BUDGET_USD", raising=False)
        monkeypatch.delenv("LLM_MONTHLY_BUDGET_USD", raising=False)
        bt = _discovery_budget()
        assert bt.daily_limit == 10.0
        assert bt.monthly_limit == 200.0

    def test_zero_env_is_respected_not_overridden(self, monkeypatch):
        monkeypatch.setenv("LLM_DAILY_BUDGET_USD", "0.0")
        monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "0.0")
        bt = _discovery_budget()
        assert bt.daily_limit == 0.0
        assert bt.monthly_limit == 0.0

    def test_invalid_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("LLM_DAILY_BUDGET_USD", "not-a-number")
        monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "also-bad")
        bt = _discovery_budget()
        assert bt.daily_limit == 10.0
        assert bt.monthly_limit == 200.0


# ---------------------------------------------------------------------------
# WORK/STUDENT/VISITOR category routing used during discovery
# ---------------------------------------------------------------------------
class TestCategoryRouting:
    def test_work_student_visitor_categories_resolve(self):
        for kw, cat in [("TSS 482 temporary skill shortage", "WORK"),
                        ("student visa 500", "STUDENT"),
                        ("visitor visa 600 tourist", "VISITOR")]:
            assert _resolve_category(kw, None) == cat

    def test_ambiguous_ties_stay_unresolved(self):
        assert _resolve_category("visa visa", "VISITOR") is None


# ---------------------------------------------------------------------------
# Seed registry exposes WORK/STUDENT/VISITOR program anchors
# ---------------------------------------------------------------------------
class TestSeedRegistryProgramAnchors:
    def test_seed_contains_work_student_visitor_programs(self):
        from seed_registry import load_seed
        data = load_seed()
        by_id = {p["program_id"]: p for p in data["immigration_programs"]}
        for pid, cat, subclass in [
            ("WORK_482", "WORK", "482"),
            ("STUDENT_500", "STUDENT", "500"),
            ("VISITOR_600", "VISITOR", "600"),
        ]:
            assert pid in by_id, f"missing program row {pid}"
            assert by_id[pid]["visa_category"] == cat
            assert by_id[pid]["subclass_code"] == subclass

    def test_upsert_programs_loads_work_student_visitor(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool

        from db.models import Base, ImmigrationProgram
        from seed_registry import _SQLAlchemyAdapter, load_seed, upsert_programs

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        conn = engine.connect()
        adapter = _SQLAlchemyAdapter(conn)

        data = load_seed()
        programs = [
            p for p in data["immigration_programs"]
            if p["program_id"] in {"WORK_482", "STUDENT_500", "VISITOR_600"}
        ]
        count = upsert_programs(adapter, programs)
        from sqlalchemy import text
        user_conn = conn.execute(
            text("SELECT program_id, visa_category, subclass_code "
                 "FROM immigration_programs ORDER BY program_id")
        )
        rows = {(r[0], r[1], r[2]) for r in user_conn.fetchall()}
        assert len(rows) == count == 3
        assert ("WORK_482", "WORK", "482") in rows
        assert ("STUDENT_500", "STUDENT", "500") in rows
        assert ("VISITOR_600", "VISITOR", "600") in rows
        conn.close()
