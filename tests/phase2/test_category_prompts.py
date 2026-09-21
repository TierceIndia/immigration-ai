"""Tests that the visa_category → prompt selection is deterministic.

Covers Phase 4: a Student/WORK/VISITOR source must never accidentally use the
Skilled extraction prompt. The extraction entry point derives visa_category
from the program via the source document, then selects the correct prompt.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "phase2"))

from llm_client import PROMPT_VERSION, MockLLMClient, _get_prompt_path  # noqa: E402
from db import init_schema, get_connection  # noqa: E402

PROMPTS_DIR = ROOT / "prompts"


def _prompt_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("category,expected_subdir", [
    ("SKILLED", "skilled"),
    ("WORK", "work"),
    ("STUDENT", "student"),
    ("VISITOR", "visitor"),
])
def test_prompt_path_is_category_specific(category, expected_subdir):
    """Each visa category resolves to its own prompt directory."""
    path = _get_prompt_path(category)
    expected = PROMPTS_DIR / expected_subdir / f"{PROMPT_VERSION}.md"
    assert Path(path).resolve() == expected.resolve()
    assert Path(path).exists(), f"Prompt file missing for {category}"


def test_prompt_path_falls_back_to_base_when_unknown():
    """Unknown/legislation categories fall back to the generic prompt."""
    path = _get_prompt_path("LEGISLATION")
    base = PROMPTS_DIR / f"{PROMPT_VERSION}.md"
    if base.exists():
        # LEGISLATION has no dedicated dir → falls back to generic
        assert Path(path).resolve() == base.resolve()
    else:
        assert Path(path).exists()


def test_prompt_path_none_falls_back_to_base():
    path = _get_prompt_path(None)
    base = PROMPTS_DIR / f"{PROMPT_VERSION}.md"
    if base.exists():
        assert Path(path).resolve() == base.resolve()


def test_skilled_and_student_prompts_differ():
    """Skilled and Student prompts must not be identical (guards misuse)."""
    skilled = _prompt_text(_get_prompt_path("SKILLED"))
    student = _prompt_text(_get_prompt_path("STUDENT"))
    assert skilled != student, "Skilled and Student prompts must differ"


def test_real_client_selects_seeded_prompt_by_category(monkeypatch):
    """An OpenAI client for a Student source loads the Student prompt, not Skilled."""
    # Avoid requiring a real API key in tests by stubbing key presence.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from llm_client import OpenAILLMClient

    # Add a per-category marker to distinguish prompts (guard against all being equal)
    skilled_text = _prompt_text(_get_prompt_path("SKILLED"))
    student_text = _prompt_text(_get_prompt_path("STUDENT"))

    client = OpenAILLMClient(api_key="test-key", model="gpt-4o", visa_category="STUDENT")
    assert client._system_prompt == student_text


def test_mock_client_receives_visa_category():
    """A test double forwards the visa_category into prompt selection."""
    client = MockLLMClient(visa_category="WORK")
    assert client.visa_category == "WORK"


def test_runtime_prompt_selection_is_deterministic():
    """Repeated resolution yields identical prompt for the same category."""
    a = _get_prompt_path("STUDENT")
    b = _get_prompt_path("STUDENT")
    assert a == b
    # Category is case-insensitive at directory level (lowercased).
    assert _get_prompt_path("student") == _get_prompt_path("STUDENT")
