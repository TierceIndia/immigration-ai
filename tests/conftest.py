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
# must never make real LLM calls. Pre-seed the model to the deterministic mock
# ONCE at module load (before any app module is imported). Because dotenv
# load_dotenv() does not override vars already present in the environment, a
# local .env's EXTRACTION_MODEL / provider API keys can never leak into tests.
# ---------------------------------------------------------------------------
os.environ.setdefault("TESTING", "true")
os.environ["EXTRACTION_MODEL"] = "mock-llm-v1"
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
# UUID validation helper (shared across all route test modules)
# ---------------------------------------------------------------------------
def parse_uuid_or_404(id_str: str):
    """Parse a string to UUID, raising ValueError if invalid."""
    from uuid import UUID
    try:
        return UUID(id_str)
    except ValueError:
        raise ValueError(f"Invalid UUID: {id_str}")
