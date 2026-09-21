"""Unit tests for OpenAILLMClient mechanics (retries, empty-response fail-safe).

These run in the default offline mode (OPENAI_API_KEY absent); every HTTP call
is monkeypatched so no real request is sent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from llm_governance import LLMGovernance, ResponseCache, UsageLog
from phase2.llm_client import (
    PROMPT_VERSION, OPENAI_PROVIDER, SCHEMA_VERSION,
    MockLLMClient, OpenAILLMClient,
)


def _dummy_client():
    return OpenAILLMClient(
        api_key="test-dummy-key", model="gpt-4o-mini",
        governance=LLMGovernance(usage_log=UsageLog()),
    )


# ------------------------------------------------------------------
# Retry on transient 5xx
# ------------------------------------------------------------------
class TestOpenAIRetry:
    @patch("requests.post", autospec=True)
    def test_retries_then_succeeds_on_500(self, mock_post):
        resp_500 = MagicMock(status_code=500, headers={})
        resp_ok = MagicMock(status_code=200)
        resp_ok.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_post.side_effect = [resp_500, resp_ok]
        client = _dummy_client()
        with patch("time.sleep"):
            result = client.extract({"chunks": []})
        assert result == '{"ok": true}'
        assert mock_post.call_count == 2

    @patch("requests.post", autospec=True)
    def test_raises_after_all_retries_exhausted(self, mock_post):
        mock_post.return_value = MagicMock(status_code=500, headers={})
        client = _dummy_client()
        with patch("time.sleep"):
            with pytest.raises(RuntimeError, match="server error"):
                client.extract({"chunks": []})
        assert mock_post.call_count == 4  # initial + 3 retries


# ------------------------------------------------------------------
# OpenAI 4xx (non-429) fails immediately (no infinite retry loop)
# ------------------------------------------------------------------
class TestOpenAIPermanentErrors:
    @patch("requests.post", autospec=True)
    def test_auth_failure_401_fails_immediately(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=401,
            json=MagicMock(return_value={"error": {"code": "invalid_api_key"}}),
            raise_for_status=MagicMock(side_effect=Exception("401 Unauthorized")),
        )
        client = _dummy_client()
        with pytest.raises(Exception, match="401"):
            client.extract({"chunks": []})
        assert mock_post.call_count == 1  # no retries


# ------------------------------------------------------------------
# Empty / malformed response fails safely (no fabricated requirement)
# ------------------------------------------------------------------
class TestOpenAIFailSafe:
    @patch("requests.post", autospec=True)
    def test_empty_choices_raises_runtime_error(self, mock_post):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"choices": [], "usage": {"prompt_tokens": 1}}
        mock_post.return_value = resp
        client = _dummy_client()
        with pytest.raises(RuntimeError, match="empty extraction response"):
            client.extract({"chunks": []})

    @patch("requests.post", autospec=True)
    def test_empty_content_raises_runtime_error(self, mock_post):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "choices": [{"message": {"content": ""}}],
            "usage": {"prompt_tokens": 1},
        }
        mock_post.return_value = resp
        client = _dummy_client()
        with pytest.raises(RuntimeError, match="empty extraction response"):
            client.extract({"chunks": []})


# ------------------------------------------------------------------
# Governance wiring
# ------------------------------------------------------------------
class TestOpenAIGovernance:
    @patch("requests.post", autospec=True)
    def test_second_call_served_from_cache(self, mock_post):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "choices": [{"message": {"content": '{"cached": true}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_post.return_value = resp
        client = _dummy_client()
        input_ctx = {"chunks": [{"raw_text": "must be under 45 years"}]}
        with patch("time.sleep"):
            r1 = client.extract(input_ctx)
        with patch("time.sleep"):
            r2 = client.extract(input_ctx)
        assert r1 == r2 == '{"cached": true}'
        # Only one HTTP call because second hit governance cache
        assert mock_post.call_count == 1
        # Cache hit means only one real request was logged; the second call was
        # served from ResponseCache (no new usage entry).
        totals = client.governance.usage_log.totals()
        assert totals["requests"] == 1
        # Governance response cache now holds the entry for identical inputs.
        cached = client.governance.cache.get(
            "gpt-4o-mini", client._messages(input_ctx),
            prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION,
            temperature=0.0, provider=OPENAI_PROVIDER,
        )
        assert cached == '{"cached": true}'


# ------------------------------------------------------------------
# Model validation at construction
# ------------------------------------------------------------------
class TestOpenAIModelValidation:
    def test_non_openai_model_rejected(self):
        with pytest.raises(ValueError, match="Unsupported extraction model"):
            OpenAILLMClient(api_key="dummy", model="gemini-flash-lite-latest")

    def test_mock_model_rejected(self):
        with pytest.raises(ValueError, match="Unsupported extraction model"):
            OpenAILLMClient(api_key="dummy", model="mock-llm-v1")


# ------------------------------------------------------------------
# MockLLMClient accepts ignored model arg (drop-in for get_client signature)
# ------------------------------------------------------------------
class TestMockLLMClientSignature:
    def test_accepts_model_arg(self):
        client = MockLLMClient(model="gpt-4o-mini", visa_category="SKILLED")
        assert client.visa_category == "SKILLED"
        assert client.model_name == "mock-llm-v1"  # class default, not overridden

    def test_positional_model_arg_accepted(self):
        client = MockLLMClient("gpt-4o-mini")
        assert client.model_name == "mock-llm-v1"
