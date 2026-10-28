"""Regression test for the Gemini free-tier quota fix (Part 2a - caching).

Before this fix, get_client() built a brand-new, empty-state LLMGovernance()
on every single call. A sync run that extracts many documents/sources in the
same process therefore never reused the response cache across those
get_client() calls - only repeat extract() calls on the SAME client instance
ever hit the cache. Since production resolves a fresh client per document
(see utils/fetch_document.py::run_requirement_extraction), the real-world
caching benefit was far smaller than it looked.

get_shared_governance() fixes this: production call sites pass one shared
instance into get_client(governance=...), so two independent clients built
from two separate get_client() calls still share the same response cache and
see zero new LLM calls for identical content.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from llm_governance import get_shared_governance, reset_shared_governance
from extraction.llm_client import get_client


@pytest.fixture(autouse=True)
def _isolated_shared_governance(monkeypatch):
    """The shared governance is a process-wide singleton - reset it before and
    after each test so tests never see another test's cached/rate-limited state."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    reset_shared_governance()
    yield
    reset_shared_governance()


class TestSharedGovernanceAcrossClients:
    @patch("requests.post", autospec=True)
    def test_two_separate_get_client_calls_share_the_cache(self, mock_post):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "choices": [{"message": {"content": '{"result": "cached"}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_post.return_value = resp
        input_ctx = {"chunks": [{"raw_text": "must be under 45 years"}]}

        shared = get_shared_governance()
        client_a = get_client("gpt-4o-mini", governance=shared)
        client_b = get_client("gpt-4o-mini", governance=shared)
        assert client_a is not client_b  # two real, independent client instances

        r1 = client_a.extract(input_ctx)
        r2 = client_b.extract(input_ctx)

        assert r1 == r2 == '{"result": "cached"}'
        # The second client's call for identical content must be served from
        # the shared cache, not a second real HTTP call.
        assert mock_post.call_count == 1

    @patch("requests.post", autospec=True)
    def test_get_shared_governance_is_a_singleton_until_reset(self, mock_post):
        assert get_shared_governance() is get_shared_governance()
        reset_shared_governance()
        assert get_shared_governance() is not None

    @patch("requests.post", autospec=True)
    def test_without_shared_governance_each_client_has_its_own_cache(self, mock_post):
        """Sanity check for the OLD (broken) behavior this fix replaces: with no
        shared governance, two independently-constructed clients do NOT share a
        cache, so identical content triggers a real call from each."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "choices": [{"message": {"content": '{"result": "not cached"}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_post.return_value = resp
        input_ctx = {"chunks": [{"raw_text": "must be under 45 years"}]}

        client_a = get_client("gpt-4o-mini")
        client_b = get_client("gpt-4o-mini")

        client_a.extract(input_ctx)
        client_b.extract(input_ctx)

        assert mock_post.call_count == 2
