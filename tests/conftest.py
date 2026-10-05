"""Global test fixtures — sets TESTING=true so lifespan skips DB + scheduler init.

Every test gets its own fresh SQLite in-memory engine.
"""
from __future__ import annotations

import os
import pytest

# ---------------------------------------------------------------------------
# Hermetic test environment.
# ---------------------------------------------------------------------------
# The requirement-extraction model is env-configurable (EXTRACTION_MODEL) so a
# developer's local .env can point it at a real (billed) provider model. Tests
# must never make real LLM calls UNLESS the developer explicitly opts in.
#
#   Default (offline) mode:  EXTRACTION_MODEL is pinned to a fake OpenAI value
#   and OPENAI_API_KEY is stripped, so the OpenAI-only factory fails fast if a
#   test ever tries to reach a real provider — and the autouse fixture below
#   swaps in the deterministic MockLLMClient for every unit test.
#
#   Live mode:                set PYTEST_LLM_LIVE=1 and export OPENAI_API_KEY.
#   Integration tests marked ``integration`` (e.g. TestOpenAIRealLLM) then run
#   against the real gpt-4o-mini and are skipped when the key is absent.
#
# These lines run at module load (before any app module is imported), so a local
# .env's EXTRACTION_MODEL / provider keys can never leak in accidentally.
# ---------------------------------------------------------------------------
os.environ.setdefault("TESTING", "true")
_LIVE = os.environ.get("PYTEST_LLM_LIVE") == "1"
if _LIVE:
    os.environ.setdefault("EXTRACTION_MODEL", "gpt-4o-mini")
else:
    os.environ["EXTRACTION_MODEL"] = "gpt-4o-mini"
    for _key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ.pop(_key, None)


# ---------------------------------------------------------------------------
# Ensure lifespan never touches real DATABASE_URL during tests
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True, scope="session")
def _set_testing_env():
    os.environ["TESTING"] = "true"
    yield
    os.environ.pop("TESTING", None)


# ---------------------------------------------------------------------------
# Unit tests must never hit a real LLM: swap the OpenAI-only factory out for
# the deterministic MockLLMClient wherever production code calls get_client.
# Explicitly marked ``integration`` tests skip this swap so they can call the
# real OpenAI API (when PYTEST_LLM_LIVE=1 and OPENAI_API_KEY is exported).
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _patch_llm_client_for_unit_tests(request, monkeypatch):
    if request.node.get_closest_marker("integration"):
        yield
        return
    from extraction import llm_client
    from extraction.llm_client import MockLLMClient

    monkeypatch.setattr(llm_client, "get_client", MockLLMClient)
    # get_client is imported into these modules at their top level, so each
    # module attribute must be patched individually for the factory swap to
    # take effect at those call sites.
    import importlib
    for mod_name in ("utils.fetch_document", "source_discovery"):
        try:
            mod = importlib.import_module(mod_name)
            monkeypatch.setattr(mod, "get_client", MockLLMClient)
        except Exception:
            pass
    yield


# ---------------------------------------------------------------------------
# UUID validation helper (shared across all route test modules)
# ---------------------------------------------------------------------------
def parse_uuid_or_404(id_str: str):
    """Parse a string to UUID, raising ValueError if invalid."""
    from uuid import UUID
    try:
        return UUID(id_str)
    except ValueError:
        raise ValueError(f"Invalid UUID: {id_str}")