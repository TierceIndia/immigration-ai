"""Unit tests for tests/e2e/conftest.py's refuse_if_main_database guard.

Pure string-parsing logic, no database connection - runs in the normal
hermetic suite (not gated by POSTGRES_E2E), so a regression here is caught
immediately rather than only when someone actually points the e2e suite at
the shared main database.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import refuse_if_main_database  # noqa: E402


class TestRefuseIfMainDatabase:
    def test_refuses_the_main_database(self):
        with pytest.raises(RuntimeError, match="immigration"):
            refuse_if_main_database(
                "postgresql+psycopg2://immigration:immigration_dev@localhost:5432/immigration"
            )

    def test_allows_the_dedicated_e2e_database(self):
        refuse_if_main_database(
            "postgresql+psycopg2://immigration:immigration_dev@localhost:5432/immigration_e2e"
        )  # must not raise

    def test_allows_any_other_database_name(self):
        refuse_if_main_database(
            "postgresql+psycopg2://user:pw@some-other-host:5432/some_other_db"
        )  # must not raise

    def test_both_e2e_test_modules_actually_default_off_the_main_database(self):
        """Guards against a regression where someone edits the default URL
        back to the main database without noticing the guard would now fire."""
        tests_dir = Path(__file__).resolve().parent
        for filename in (
            "test_full_knowledge_to_assessment.py",
            "test_postgres_pipeline_lifecycle.py",
        ):
            text = (tests_dir / filename).read_text(encoding="utf-8")
            assert "/immigration_e2e" in text, (
                f"{filename} no longer defaults to the dedicated e2e database"
            )
            assert '"postgresql+psycopg2://immigration:immigration_dev@localhost:5432/immigration"' not in text, (
                f"{filename}'s default URL regressed back to the main database"
            )
