"""LLM client abstraction for requirement extraction.

PRODUCTION POLICY — OpenAI and Gemini are the supported providers.

The production extraction factory (`get_client` / `create_llm_client`) resolves to
`OpenAILLMClient` or `GeminiLLMClient` based on the configured model name, and FAILS
FAST when the configuration is invalid:

- `EXTRACTION_MODEL` unset -> default `gpt-4o-mini`.
- Missing API key for the resolved provider (`OPENAI_API_KEY` / `GEMINI_API_KEY`) ->
  RuntimeError. There is NO silent fallback to a mock or to another provider, and no
  fake requirement is ever manufactured.
- any unsupported model name (e.g. `claude-*`, or the test-only `mock-llm-v1`) -> ValueError.

Implementations:

- `OpenAILLMClient` (gpt-4o-mini, gpt-4o, ...) - requires OPENAI_API_KEY. Uses JSON
  response mode, temperature=0, exponential retries on 429/5xx with backoff, and routes
  every call through the LLM governance layer (rate limiting, budget, provider-namespaced
  response cache, usage/cost logging).

- `GeminiLLMClient` (gemini-flash-lite-latest, ...) - requires GEMINI_API_KEY (or
  GOOGLE_API_KEY). Same governance wiring as OpenAILLMClient: rate limiting, budget,
  provider-namespaced response cache, usage/cost logging, and retries on 429/5xx.

- `MockLLMClient` - TEST-ONLY deterministic double. Fully offline, no network call. It applies
  a small set of hand-written regex rules that mimic what a careful extraction model should do
  for common eligibility phrasings. It exists so the rest of the pipeline (validation,
  evidence-checking, normalization, dedup, persistence, review) and the "LLM contract tests"
  can run deterministically. It is NEVER returned by `get_client` and is never a production
  default, CLI default, scheduler default, or fallback.

- `AnthropicLLMClient` - retained for reference only; the production factory refuses it.
  Use legacy checkouts if Anthropic provider support is ever restored.

All implementations honor the same contract: given a JSON input context, return a raw string
that should be JSON matching requirements.schema.json. The caller (requirement_extractor.py) is
responsible for parsing and validating - the client itself does not validate.
"""
from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod

import dotenv

from logging_config import get_logger

logger = get_logger(__name__)

# In TESTS, dotenv loading is suppressed so a developer's .env cannot leak
# API keys into the hermetic suite (conftest strips them at module load).
# TESTING=true is the pytest convention; normal scripts/CLI still load .env.
if not os.environ.get("TESTING"):
    dotenv.load_dotenv()  # load OPENAI_API_KEY from .env if present (prod/dev only)

PROMPT_VERSION = "requirement_extraction_v2"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
OPENAI_PROVIDER = "openai"
GEMINI_PROVIDER = "gemini"
# Namespacing version for the requirement output contract (schema/requirements.schema.json).
SCHEMA_VERSION = "requirements.schema.json@v1"


def _get_prompt_path(visa_category: str | None = None) -> str:
    """Get the path to the prompt file for the given visa category.
    
    Falls back to the base prompt if category-specific prompt doesn't exist.
    """
    base_dir = os.path.join(os.path.dirname(__file__), "..", "..", "prompts")
    if visa_category:
        category_path = os.path.join(base_dir, visa_category.lower(), f"{PROMPT_VERSION}.md")
        if os.path.exists(category_path):
            return category_path
    # Fallback to flat prompt (backward compatibility)
    return os.path.join(base_dir, f"{PROMPT_VERSION}.md")


class LLMClient(ABC):
    model_name: str

    @abstractmethod
    def extract(self, input_context: dict) -> str:
        """Return a raw string that should be JSON matching requirements.schema.json."""
        raise NotImplementedError

    def get_system_prompt(self, visa_category: str | None = None) -> str:
        """Get the system prompt for the given visa category.
        Subclasses can override to provide category-specific prompts."""
        return self._system_prompt


class MockLLMClient(LLMClient):
    """Deterministic rule-based stand-in for the LLM. Never invents a value it can't find a
    textual match for; returns UNKNOWN/None fields otherwise - same discipline the real prompt
    demands. TEST-ONLY: never returned by ``get_client`` and never a production default.

    The optional ``model``/``governance`` arguments are accepted (and ignored) so the
    class can substitute for the real ``get_client`` factory signature in unit tests
    without breaking call sites that pass a model name or a shared governance instance.
    """

    model_name = "mock-llm-v1"

    def __init__(self, model: str | None = None, visa_category: str | None = None,
                 governance=None):
        self.visa_category = visa_category
        self._system_prompt = ""

    def get_system_prompt(self, visa_category: str | None = None) -> str:
        return ""

    _AGE_LT = re.compile(r"(?:aged?\s+)?under\s+(\d+)", re.IGNORECASE)
    _AGE_LTE = re.compile(r"(\d+)\s*years?\s+of\s+age\s+or\s+(?:younger|under)", re.IGNORECASE)
    _EXPERIENCE_GTE = re.compile(
        r"at least (?:\w+\s*\()?(\d+)\)?\s*years? of (?:relevant\s+)?(?:full-time\s+)?work experience",
        re.IGNORECASE,
    )
    _POINTS_GTE = re.compile(r"(?:score|at least)\s+(?:of\s+)?(\d+)\s+points", re.IGNORECASE)
    _POINTS_HIGHER = re.compile(r"score may be higher than\s+(\d+)\s+points", re.IGNORECASE)
    _ENGLISH_MENTION = re.compile(r"(?:at least\s+)?competent english|approved equivalent test", re.IGNORECASE)
    _ENGLISH_EXEMPT = re.compile(r"exempt from the english language requirement", re.IGNORECASE)
    _SKILLS_ASSESSMENT = re.compile(r"skills\s+assessment", re.IGNORECASE)
    _SKILLS_ASSESSMENT_EXCEPTION = re.compile(r"unless the assessing authority", re.IGNORECASE)
    _OCCUPATION_LIST = re.compile(
        r"(?:occupation|nominated\s+occupation)\s+must\s+be\s+on\s+the\s+(?:relevant\s+list|[A-Za-z][A-Za-z\- ]+\s+List)",
        re.IGNORECASE,
    )
    _HEALTH = re.compile(r"health\s+requirement", re.IGNORECASE)
    _CHARACTER = re.compile(r"character\s+requirement", re.IGNORECASE)
    _INVITATION_REQUIRED = re.compile(
        r"(?:only\s+apply.*?if\s+we\s+invite\s+you|cannot\s+apply.*?(?:invite|invited)\s+to\s+apply)",
        re.IGNORECASE,
    )
    _INVITATION_VALIDITY = re.compile(r"invitation is valid for (\d+) days", re.IGNORECASE)
    _NOMINATION_NOT_REQUIRED = re.compile(r"does not require state or territory nomination", re.IGNORECASE)
    _DEBT = re.compile(r"owe\s+the\s+Australian\s+Government\s+money", re.IGNORECASE)
    _VALUES_STATEMENT = re.compile(r"Australian\s+Values\s+Statement|Life\s+in\s+Australia\s+booklet", re.IGNORECASE)
    _VISA_STATUS = re.compile(r"(?:substantive\s+visa|Bridging\s+visa\s+[ABC])", re.IGNORECASE)
    _REGIONAL_VISA_YEARS = re.compile(r"must have held that visa for at least\s+(\d+)\s+years", re.IGNORECASE)
    _VISA_HISTORY = re.compile(r"visa\s+(?:cancelled|refused)|immigration\s+history", re.IGNORECASE)
    _VISA_VALIDITY = re.compile(r"travel to and from australia for (\w+) years?", re.IGNORECASE)
    _FEE = re.compile(r"application charge|application fee", re.IGNORECASE)
    _FAMILY = re.compile(r"partner and dependent children", re.IGNORECASE)
    _LEGAL_REF = re.compile(
        r"(Schedule \d+, Subclass \d+ of the Migration Regulations 1994)", re.IGNORECASE
    )

    _WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                      "seven": 7, "eight": 8, "nine": 9, "ten": 10}

    def extract(self, input_context: dict) -> str:
        primary = input_context["primary_chunk"]
        text = primary["raw_text"]
        chunk_id = primary["chunk_id"]
        heading_path = primary.get("heading_path", "") or ""
        chunk_type = primary.get("chunk_type", "PARAGRAPH")

        requirements = []
        is_eligibility = "eligib" in heading_path.lower()

        if is_eligibility:
            m = self._AGE_LTE.search(text)
            if m:
                value = int(m.group(1))
                requirements.append(self._req(
                    "AGE", "Age requirement", text, chunk_id, "LTE", value, "years", True, "CRITICAL",
                    evidence_type="DIRECT_TEXT",
                ))
            else:
                m = self._AGE_LT.search(text)
                if m:
                    value = int(m.group(1))
                    requirements.append(self._req(
                        "AGE", "Age requirement", text, chunk_id, "LT", value, "years", True, "CRITICAL",
                        evidence_type="DIRECT_TEXT",
                    ))

            if "still apply for the visa if you turn 45 after we invite you" in text.lower():
                requirements.append({
                    **self._simple("AGE", "Age-at-invitation exception", text, chunk_id,
                                   "GTE", 45, "years", False, "IMPORTANT", "applicant.age"),
                    "content_kind": "EXCEPTION",
                })

        m = self._EXPERIENCE_GTE.search(text)
        if m:
            value = int(m.group(1))
            requirements.append(self._req(
                "WORK_EXPERIENCE", "Work experience requirement", text, chunk_id, "GTE", value,
                "years", True, "IMPORTANT", evidence_type="DIRECT_TEXT",
            ))

        m = self._POINTS_GTE.search(text)
        if m:
            value = int(m.group(1))
            requirements.append(self._req(
                "POINTS", "Points test minimum score", text, chunk_id, "GTE", value, "points",
                True, "CRITICAL", evidence_type="DIRECT_TEXT",
            ))

        m = self._POINTS_HIGHER.search(text)
        if m:
            requirements.append(self._simple(
                "POINTS", "Invitation-specific points threshold", text, chunk_id, "GTE",
                int(m.group(1)), "points", True, "IMPORTANT", "applicant.required_points",
            ))

        if self._ENGLISH_EXEMPT.search(text):
            requirements.append({
                "category": "ENGLISH_LANGUAGE", "name": "English language exemption",
                "description": text, "content_kind": "EXCEPTION", "field": "applicant.english_test_result",
                "operator": "NOT_REQUIRED", "value": None, "unit": None, "mandatory": False,
                "condition": None, "exception": None, "applies_to": None, "criticality": "IMPORTANT",
                "confidence": 0.85,
                "evidence": [{"chunk_id": chunk_id, "evidence_text": text, "evidence_type": "DIRECT_TEXT"}],
                "references": [],
            })
        elif self._ENGLISH_MENTION.search(text):
            requirements.append(self._simple(
                "ENGLISH_LANGUAGE", "English language requirement", text, chunk_id, "GTE",
                "competent", None, True, "CRITICAL", "applicant.english_level",
            ))

        if self._SKILLS_ASSESSMENT_EXCEPTION.search(text):
            requirements.append(self._simple("SKILLS_ASSESSMENT", "Skills assessment currency", text,
                                                chunk_id, "REQUIRED", None, None, True, "IMPORTANT",
                                                "applicant.skills_assessment_validity"))
        elif self._SKILLS_ASSESSMENT.search(text) and ("must" in text.lower() and "have" in text.lower()):
            requirements.append(self._simple("SKILLS_ASSESSMENT", "Skills assessment required", text,
                                                chunk_id, "REQUIRED", True, None, True, "CRITICAL",
                                                "applicant.skills_assessment"))

        if "obtained within the 60 day invitation period" in text.lower():
            requirements.append(self._simple(
                "SKILLS_ASSESSMENT", "Skills assessment invitation-period acceptance", text,
                chunk_id, "LTE", 60, "days", False, "NORMAL",
                "applicant.skills_assessment_acceptance_window",
            ))
        if "skills assessment must have been obtained in the 3 years" in text.lower():
            requirements.append(self._simple(
                "SKILLS_ASSESSMENT", "Skills assessment age", text, chunk_id, "LTE", 3,
                "years", True, "IMPORTANT", "applicant.skills_assessment_age",
            ))

        m = self._OCCUPATION_LIST.search(text)
        if m:
            requirements.append(self._simple("OCCUPATION", "Occupation list requirement", text, chunk_id,
                                                "IN", ["skilled_occupations_list_189"], None, True, "CRITICAL",
                                                "applicant.occupation"))

        if self._HEALTH.search(text):
            requirements.append(self._simple("HEALTH", "Health requirement", text, chunk_id,
                                                "REQUIRED", True, None, True, "CRITICAL",
                                                "applicant.health_check"))

        if self._CHARACTER.search(text):
            requirements.append(self._simple("CHARACTER", "Character requirement", text, chunk_id,
                                                "REQUIRED", True, None, True, "CRITICAL",
                                                "applicant.character_check"))

        if self._INVITATION_REQUIRED.search(text):
            requirements.append(self._simple("INVITATION", "Invitation required", text, chunk_id,
                                                "EQ", True, None, True, "CRITICAL",
                                                "applicant.invited_to_apply"))

        m = self._INVITATION_VALIDITY.search(text)
        if m:
            requirements.append(self._simple("INVITATION", "Invitation validity period", text, chunk_id,
                                                "EQ", int(m.group(1)), "days", True, "IMPORTANT",
                                                "invitation.validity_days"))

        if self._NOMINATION_NOT_REQUIRED.search(text):
            requirements.append(self._simple("NOMINATION", "State/territory nomination not required",
                                                text, chunk_id, "NOT_REQUIRED", None, None, None,
                                                "IMPORTANT", "nomination.required"))

        m = self._VISA_VALIDITY.search(text)
        if m:
            word = m.group(1).lower()
            value = self._WORD_NUMBERS.get(word, None)
            if value is not None:
                requirements.append({
                    "category": "VISA_STATUS", "name": "Visa travel validity", "description": text,
                    "content_kind": "INFORMATIONAL", "field": "visa.travel_validity_years",
                    "operator": "EQ", "value": value, "unit": "years", "mandatory": None,
                    "condition": None, "exception": None, "applies_to": None, "criticality": "NORMAL",
                    "confidence": 0.8,
                    "evidence": [{"chunk_id": chunk_id, "evidence_text": text, "evidence_type": "DIRECT_TEXT"}],
                    "references": [],
                })

        if self._FEE.search(text):
            requirements.append({
                "category": "FINANCIAL", "name": "Application fee", "description": text,
                "content_kind": "REQUIREMENT", "field": "application.fee", "operator": "UNKNOWN",
                "value": None, "unit": "AUD", "mandatory": True, "condition": None, "exception": None,
                "applies_to": None, "criticality": "NORMAL", "confidence": 0.6,
                "evidence": [{"chunk_id": chunk_id, "evidence_text": text, "evidence_type": "DIRECT_TEXT"}],
                "references": [],
            })

        if self._FAMILY.search(text):
            requirements.append(self._simple("FAMILY", "Family member inclusion criteria", text, chunk_id,
                                                "REQUIRED", None, None, None, "NORMAL",
                                                "family_member.public_interest_criteria"))

        if self._DEBT.search(text):
            requirements.append(self._simple("DEBT", "Australian Government debt repayment", text,
                                                chunk_id, "EQ", False, None, True, "IMPORTANT",
                                                "applicant.au_govt_debt"))

        if self._VALUES_STATEMENT.search(text):
            requirements.append(self._simple("VALUES_STATEMENT", "Australian values statement", text,
                                                chunk_id, "REQUIRED", True, None, True, "IMPORTANT",
                                                "applicant.values_statement"))

        if self._VISA_STATUS.search(text):
            requirements.append(self._simple("VISA_STATUS", "Visa status requirement", text,
                                                chunk_id, "IN", ["substantive", "BVA", "BVB", "BVC"], None, True, "IMPORTANT",
                                                "applicant.current_visa_status"))

        m = self._REGIONAL_VISA_YEARS.search(text)
        if m:
            requirements.append(self._simple(
                "VISA_STATUS", "Regional provisional visa holding period", text, chunk_id,
                "GTE", int(m.group(1)), "years", True, "IMPORTANT",
                "applicant.held_regional_visa_years",
            ))

        if self._VISA_HISTORY.search(text):
            requirements.append(self._simple("VISA_HISTORY", "Visa cancellation/refusal history", text,
                                                chunk_id, "EQ", False, None, True, "IMPORTANT",
                                                "applicant.visa_cancelled_or_refused"))

        legal_refs = self._LEGAL_REF.findall(text)
        if legal_refs:
            requirements.append({
                "category": "OTHER", "name": "Legal basis cross-reference", "description": text,
                "content_kind": "CROSS_REFERENCE", "field": None, "operator": "UNKNOWN", "value": None,
                "unit": None, "mandatory": None, "condition": None, "exception": None, "applies_to": None,
                "criticality": "IMPORTANT", "confidence": 0.8,
                "evidence": [{"chunk_id": chunk_id, "evidence_text": text, "evidence_type": "CROSS_REFERENCE"}],
                "references": [{"reference_type": "LEGAL_CROSS_REFERENCE", "target_text": legal_refs[0],
                                  "status": "UNRESOLVED"}],
            })

        if chunk_type == "TABLE" and "points" in heading_path.lower():
            requirements.append({
                "category": "POINTS",
                "name": "Points table",
                "description": "Points allocation table.",
                "content_kind": "FACT",
                "field": None,
                "operator": "UNKNOWN",
                "value": None,
                "unit": "points",
                "mandatory": None,
                "condition": None,
                "exception": None,
                "applies_to": None,
                "criticality": "NORMAL",
                "confidence": 0.6,
                "evidence": [{"chunk_id": chunk_id, "evidence_text": text[:200], "evidence_type": "TABLE"}],
                "references": [],
            })

        if chunk_type == "LIST" and "document" in heading_path.lower():
            requirements.append({
                "category": "DOCUMENT",
                "name": "Required documents",
                "description": "List of documents applicants must provide.",
                "content_kind": "REQUIREMENT",
                "field": "applicant.documents",
                "operator": "REQUIRED",
                "value": None,
                "unit": None,
                "mandatory": True,
                "condition": None,
                "exception": None,
                "applies_to": None,
                "criticality": "IMPORTANT",
                "confidence": 0.7,
                "evidence": [{"chunk_id": chunk_id, "evidence_text": text[:200], "evidence_type": "LIST"}],
                "references": [],
            })

        return json.dumps({"requirements": requirements})

    @staticmethod
    def _simple(category, name, full_text, chunk_id, operator, value, unit, mandatory, criticality, field_name):
        sentence = full_text.strip()
        return {
            "category": category, "name": name, "description": sentence, "content_kind": "REQUIREMENT",
            "field": field_name, "operator": operator, "value": value, "unit": unit,
            "mandatory": mandatory, "condition": None, "exception": None, "applies_to": None,
            "criticality": criticality, "confidence": 0.85,
            "evidence": [{"chunk_id": chunk_id, "evidence_text": sentence, "evidence_type": "DIRECT_TEXT"}],
            "references": [],
        }

    @staticmethod
    def _req(category, name, full_text, chunk_id, operator, value, unit, mandatory, criticality,
              evidence_type="DIRECT_TEXT"):
        # Evidence text must be an exact substring of the source - use the matched sentence only
        # (first sentence containing the match, trimmed) rather than the whole chunk, to mimic a
        # precise real extractor.
        sentence = full_text.strip()
        return {
            "category": category,
            "name": name,
            "description": sentence,
            "content_kind": "REQUIREMENT",
            "field": {
                "AGE": "applicant.age",
                "WORK_EXPERIENCE": "applicant.work_experience_years",
                "POINTS": "applicant.points_score",
                "ENGLISH_LANGUAGE": "applicant.english_test_result",
            }.get(category),
            "operator": operator,
            "value": value,
            "unit": unit,
            "mandatory": mandatory,
            "condition": None,
            "exception": None,
            "applies_to": None,
            "criticality": criticality,
            "confidence": 0.9,
            "evidence": [{"chunk_id": chunk_id, "evidence_text": sentence, "evidence_type": evidence_type}],
            "references": [],
        }


class OpenAILLMClient(LLMClient):
    model_name = DEFAULT_OPENAI_MODEL

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 visa_category: str | None = None, governance=None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if model:
            self.model_name = model
        if not self._is_openai_model(self.model_name):
            raise ValueError(
                f"Unsupported extraction model {self.model_name!r}: only OpenAI models "
                "are supported (e.g. gpt-4o-mini, gpt-4o)."
            )
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required for real LLM extraction. Set the OPENAI_API_KEY "
                "environment variable (or pass api_key= explicitly). Never hardcode the key "
                "in source or seed files; there is no silent fallback to a mock."
            )
        self.visa_category = visa_category
        if governance is None:
            from llm_governance import LLMGovernance
            governance = LLMGovernance()
        self.governance = governance
        self._gov_key = visa_category or "phase2-requirement-extraction"
        prompt_path = _get_prompt_path(visa_category)
        with open(prompt_path, "r", encoding="utf-8") as f:
            self._system_prompt = f.read()

    @staticmethod
    def _is_openai_model(model: str) -> bool:
        return model.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-", "ft:gpt-"))

    def _messages(self, input_context: dict) -> list[dict]:
        return [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": json.dumps(input_context, indent=2)},
        ]

    def _post_chat(self, messages: list[dict]) -> tuple[dict, int]:
        """Call the OpenAI chat completions API with exponential backoff.

        Retries only transient 429/5xx responses. Permanent configuration errors (4xx auth /
        invalid model / invalid request) fail immediately and are never retried indefinitely.
        Returns (parsed JSON body, retries_used).
        """
        import time  # local import: only needed if this client is actually used
        import requests  # local import: only needed if this client is actually used

        max_retries = 3
        backoff_seconds = [5, 15, 45]
        resp = None
        retries = 0
        for attempt in range(max_retries + 1):
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model_name,
                    "max_tokens": 2000,
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                    "messages": messages,
                },
                timeout=120,
            )
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < max_retries:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else backoff_seconds[attempt]
                time.sleep(min(wait, 60))
                retries += 1
                continue
            break

        if resp.status_code == 429:
            try:
                detail = resp.json().get("error", {}).get("code", "")
            except Exception:
                detail = ""
            hint = ""
            if detail == "insufficient_quota":
                hint = (" This account has no API credits - add billing at "
                        "platform.openai.com/settings/organization/billing.")
            raise RuntimeError(
                f"OpenAI API rate limit/quota error (429, code={detail}).{hint} "
                f"Model used: {self.model_name}."
            )
        if resp.status_code >= 500:
            raise RuntimeError(
                f"OpenAI API server error ({resp.status_code}) after retries. "
                f"Model: {self.model_name}. Transient - re-run later."
            )
        resp.raise_for_status()
        return resp.json(), retries

    def extract(self, input_context: dict) -> str:
        import time  # local import: only needed if this client is actually used

        messages = self._messages(input_context)
        gate = self.governance.pre_request(
            key=self._gov_key, model=self.model_name, messages=messages,
            prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION, temperature=0.0,
            provider=OPENAI_PROVIDER,
        )
        if gate.get("blocked"):
            raise RuntimeError(
                f"OpenAI extraction blocked by LLM governance "
                f"({gate.get('reason')}). Model: {self.model_name}."
            )
        if gate.get("cached"):
            logger.info(
                "OpenAI extraction served from governance cache",
                extra={"model": self.model_name, "provider": OPENAI_PROVIDER,
                       "prompt_version": PROMPT_VERSION},
            )
            return gate["cached_response"]

        start = time.perf_counter()
        try:
            data, retries = self._post_chat(messages)
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000.0
            self.governance.post_request(
                key=self._gov_key, model=self.model_name, messages=messages, response=None,
                tokens_in=0, tokens_out=0, latency_ms=latency_ms, retries=0,
                error=f"{exc.__class__.__name__}: {exc}",
                prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION,
                temperature=0.0, provider=OPENAI_PROVIDER,
            )
            raise

        latency_ms = (time.perf_counter() - start) * 1000.0
        usage = data.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens", 0) or 0)
        tokens_out = int(usage.get("completion_tokens", 0) or 0)

        choices = data.get("choices", [])
        content = choices[0]["message"]["content"] if choices else ""
        if not content or not content.strip():
            raise RuntimeError(
                f"OpenAI returned an empty extraction response. Model: {self.model_name}. "
                "The extraction failed safely; no requirement was manufactured."
            )

        self.governance.post_request(
            key=self._gov_key, model=self.model_name, messages=messages, response=content,
            tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms, retries=retries,
            prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION,
            temperature=0.0, provider=OPENAI_PROVIDER,
        )
        logger.info(
            "OpenAI extraction request completed",
            extra={"model": self.model_name, "provider": OPENAI_PROVIDER,
                   "prompt_version": PROMPT_VERSION, "tokens_in": tokens_in,
                   "tokens_out": tokens_out, "latency_ms": round(latency_ms, 1),
                   "retries": retries},
        )
        return content


class AnthropicLLMClient(LLMClient):
    model_name = "claude-sonnet-4-6"

    def __init__(self, api_key: str | None = None, model: str | None = None, visa_category: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if model:
            self.model_name = model
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Set the environment variable or pass api_key= "
                "explicitly. (Never hardcode the key in source or seed files.)"
            )
        prompt_path = _get_prompt_path(visa_category)
        with open(prompt_path, "r", encoding="utf-8") as f:
            self._system_prompt = f.read()

    def extract(self, input_context: dict) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key, max_retries=3, timeout=180.0)
        message = client.messages.create(
            model=self.model_name,
            max_tokens=4000,
            temperature=0.0,
            system=self._system_prompt,
            messages=[{"role": "user", "content": json.dumps(input_context, indent=2)}],
        )
        return "\n".join(block.text for block in message.content if block.type == "text")


class GeminiLLMClient(LLMClient):
    model_name = "gemini-flash-lite-latest"

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 visa_category: str | None = None, governance=None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if model:
            self.model_name = model
        if not self._is_gemini_model(self.model_name):
            raise ValueError(
                f"Unsupported extraction model {self.model_name!r}: GeminiLLMClient only "
                "supports Gemini models (e.g. gemini-flash-lite-latest, gemini-1.5-pro)."
            )
        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set. Set the environment variable (or .env) or pass api_key= "
                "explicitly. (Never hardcode the key in source or seed files.)"
            )
        self.visa_category = visa_category
        if governance is None:
            from llm_governance import LLMGovernance
            governance = LLMGovernance()
        self.governance = governance
        self._gov_key = visa_category or "phase2-requirement-extraction"
        prompt_path = _get_prompt_path(visa_category)
        with open(prompt_path, "r", encoding="utf-8") as f:
            self._system_prompt = f.read()

    @staticmethod
    def _is_gemini_model(model: str) -> bool:
        return model.startswith("gemini-")

    def _call_gemini(self, input_context: dict) -> tuple[str, int, int, int]:
        """Call the Gemini API with exponential backoff.

        Returns (raw text response, tokens_in, tokens_out, retries_used).
        """
        import time  # local import: only needed if this client is actually used
        import requests  # local import: only needed if this client is actually used

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        payload = {
            "systemInstruction": {"parts": [{"text": self._system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(input_context, indent=2)}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 4000,
                "responseMimeType": "application/json",
            },
        }

        max_retries = 3
        backoff_seconds = [5, 15, 45]
        resp = None
        retries = 0
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=180)
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt < max_retries:
                    time.sleep(backoff_seconds[attempt])
                    retries += 1
                    continue
                raise RuntimeError(
                    f"Gemini API unreachable after retries ({exc.__class__.__name__}). "
                    f"Model: {self.model_name}."
                ) from exc
            transient = resp.status_code == 429 or resp.status_code >= 500
            if transient and attempt < max_retries:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else backoff_seconds[attempt]
                time.sleep(min(wait, 60))
                retries += 1
                continue
            break

        if resp.status_code == 429:
            raise RuntimeError(
                f"Gemini API rate limit exceeded (429). Model: {self.model_name}. "
                f"Free tier has per-minute/per-day caps - wait and retry."
            )
        if resp.status_code >= 500:
            raise RuntimeError(
                f"Gemini API server error ({resp.status_code}) after retries. "
                f"Model: {self.model_name}. Transient - re-run later."
            )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            block_reason = data.get("promptFeedback", {}).get("blockReason")
            raise RuntimeError(
                f"Gemini returned no candidates"
                f"{' (blocked: ' + block_reason + ')' if block_reason else ''}. "
                f"Raw: {json.dumps(data)[:300]}"
            )
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "\n".join(p.get("text", "") for p in parts)
        usage = data.get("usageMetadata") or {}
        tokens_in = int(usage.get("promptTokenCount", 0) or 0)
        tokens_out = int(usage.get("candidatesTokenCount", 0) or 0)
        return text, tokens_in, tokens_out, retries

    def extract(self, input_context: dict) -> str:
        import time  # local import: only needed if this client is actually used

        messages = self._messages_for_cache(input_context)
        gate = self.governance.pre_request(
            key=self._gov_key, model=self.model_name, messages=messages,
            prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION, temperature=0.0,
            provider=GEMINI_PROVIDER,
        )
        if gate.get("blocked"):
            raise RuntimeError(
                f"Gemini extraction blocked by LLM governance "
                f"({gate.get('reason')}). Model: {self.model_name}."
            )
        if gate.get("cached"):
            logger.info(
                "Gemini extraction served from governance cache",
                extra={"model": self.model_name, "provider": GEMINI_PROVIDER,
                       "prompt_version": PROMPT_VERSION},
            )
            return gate["cached_response"]

        start = time.perf_counter()
        try:
            content, tokens_in, tokens_out, retries = self._call_gemini(input_context)
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000.0
            self.governance.post_request(
                key=self._gov_key, model=self.model_name, messages=messages, response=None,
                tokens_in=0, tokens_out=0, latency_ms=latency_ms, retries=0,
                error=f"{exc.__class__.__name__}: {exc}",
                prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION,
                temperature=0.0, provider=GEMINI_PROVIDER,
            )
            raise

        latency_ms = (time.perf_counter() - start) * 1000.0
        if not content or not content.strip():
            raise RuntimeError(
                f"Gemini returned an empty extraction response. Model: {self.model_name}. "
                "The extraction failed safely; no requirement was manufactured."
            )

        self.governance.post_request(
            key=self._gov_key, model=self.model_name, messages=messages, response=content,
            tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms, retries=retries,
            prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION,
            temperature=0.0, provider=GEMINI_PROVIDER,
        )
        logger.info(
            "Gemini extraction request completed",
            extra={"model": self.model_name, "provider": GEMINI_PROVIDER,
                   "prompt_version": PROMPT_VERSION, "tokens_in": tokens_in,
                   "tokens_out": tokens_out, "latency_ms": round(latency_ms, 1),
                   "retries": retries},
        )
        return content

    def _messages_for_cache(self, input_context: dict) -> list[dict]:
        """Shape Gemini's system+content into the same {role, content} form the
        governance cache/rate-limit keying uses for OpenAI, so both providers hash
        equivalently for the same (system prompt, input) pair."""
        return [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": json.dumps(input_context, indent=2)},
        ]


def get_client(model: str | None = None, visa_category: str | None = None,
                governance=None) -> LLMClient:
    """Production factory — resolves to OpenAILLMClient or GeminiLLMClient.

    Resolves `model` from the argument, then `EXTRACTION_MODEL`, falling back to
    ``DEFAULT_OPENAI_MODEL``.  Any unsupported model (including the test-only
    ``mock-llm-v1`` or an unsupported provider such as Anthropic) raises immediately
    so that production never silently falls back to fake requirement output.  A
    missing API key for the resolved provider also fails fast.

    `governance` is an optional pre-built LLMGovernance instance. When omitted,
    each client builds its own fresh, empty-state instance (the old behavior) —
    callers that extract more than one document/source in the same process
    (e.g. a sync run covering many sources) should build ONE shared instance
    via `llm_governance.get_shared_governance()` and pass it here, so the
    response cache and rate limiter actually see calls across the whole run
    instead of resetting on every single `get_client()` call.
    """
    if model is None:
        model = os.getenv("EXTRACTION_MODEL", DEFAULT_OPENAI_MODEL)

    if model == MockLLMClient.model_name:
        raise ValueError(
            "mock-llm-v1 is a test-only deterministic double and must not be selected "
            "in production.  Set EXTRACTION_MODEL to a supported model "
            "(e.g. EXTRACTION_MODEL=gpt-4o-mini or EXTRACTION_MODEL=gemini-flash-lite-latest)."
        )
    if OpenAILLMClient._is_openai_model(model):
        return OpenAILLMClient(model=model, visa_category=visa_category, governance=governance)
    if GeminiLLMClient._is_gemini_model(model):
        return GeminiLLMClient(model=model, visa_category=visa_category, governance=governance)
    raise ValueError(
        f"Unsupported extraction model {model!r}.  Supported providers are OpenAI "
        "(examples: gpt-4o-mini, gpt-4o, ft:gpt-4o-mini-...) and Gemini "
        "(examples: gemini-flash-lite-latest, gemini-1.5-pro)."
    )


# Public alias: some callers import create_llm_client; both resolve identically.
create_llm_client = get_client
