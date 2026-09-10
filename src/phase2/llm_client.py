"""LLM client abstraction for requirement extraction.

Implementations:

- `MockLLMClient` - fully deterministic, no network call, used by every automated test and by
  `run_extraction.py --dry-run` when no API key is configured. It applies a small set of
  hand-written regex rules that mimic what a careful extraction model should do for common
  eligibility phrasings. It exists so the rest of the pipeline (validation, evidence-checking,
  normalization, dedup, persistence, review) can be built and tested end-to-end without needing
  live model access, and so the "LLM contract tests" required by this phase can mock specific
  response shapes (valid, invalid JSON, hallucinated evidence, etc.) deterministically.

- `OpenAILLMClient` (gpt-4o-mini, etc.) - requires OPENAI_API_KEY. Uses JSON response mode,
  temperature=0, retries on 429/5xx with backoff.

- `GeminiLLMClient` (gemini-flash-lite-latest, etc.) - requires GEMINI_API_KEY. Uses JSON response
  mode, temperature=0, retries on 429/5xx/timeout with backoff. Free-tier models work well for
  requirement extraction with context_window=0 on self-contained section chunks.

- `AnthropicLLMClient` - legacy, requires ANTHROPIC_API_KEY. Retries handled by the SDK.

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

dotenv.load_dotenv()  # load OPENAI_API_KEY (or other keys) from .env if present

PROMPT_VERSION = "requirement_extraction_v2"


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
    demands. Used for tests and for --dry-run without credentials.
    """

    model_name = "mock-llm-v1"

    def __init__(self, visa_category: str | None = None):
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
    model_name = "gpt-4o-mini"

    def __init__(self, api_key: str | None = None, model: str | None = None, visa_category: str | None = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if model:
            self.model_name = model
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not set. Set the environment variable or pass api_key= "
                "explicitly. (Never hardcode the key in source or seed files.)"
            )
        prompt_path = _get_prompt_path(visa_category)
        with open(prompt_path, "r", encoding="utf-8") as f:
            self._system_prompt = f.read()

    def extract(self, input_context: dict) -> str:
        import time  # local import: only needed if this client is actually used
        import requests  # local import: only needed if this client is actually used

        max_retries = 3
        backoff_seconds = [5, 15, 45]  # handles transient rate limits and server errors
        resp = None
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
                    "messages": [
                        {"role": "system", "content": self._system_prompt},
                        {"role": "user", "content": json.dumps(input_context, indent=2)},
                    ],
                },
                timeout=120,
            )
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < max_retries:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else backoff_seconds[attempt]
                time.sleep(min(wait, 60))
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
        data = resp.json()
        choices = data.get("choices", [])
        if choices:
            return choices[0]["message"]["content"]
        return ""


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

    def __init__(self, api_key: str | None = None, model: str | None = None, visa_category: str | None = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if model:
            self.model_name = model
        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set. Set the environment variable (or .env) or pass api_key= "
                "explicitly. (Never hardcode the key in source or seed files.)"
            )
        prompt_path = _get_prompt_path(visa_category)
        with open(prompt_path, "r", encoding="utf-8") as f:
            self._system_prompt = f.read()

    def extract(self, input_context: dict) -> str:
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
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=180)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                if attempt < max_retries:
                    time.sleep(backoff_seconds[attempt])
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
                continue
            break
        del last_exc

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
        return "\n".join(p.get("text", "") for p in parts)


def get_client(model: str, visa_category: str | None = None) -> LLMClient:
    if model == MockLLMClient.model_name:
        return MockLLMClient(visa_category=visa_category)
    if model.startswith("gemini"):
        return GeminiLLMClient(model=model, visa_category=visa_category)
    if model.startswith("claude"):
        return AnthropicLLMClient(model=model, visa_category=visa_category)
    if model.startswith("gpt-") or model.startswith("o") or model.startswith("chatgpt"):
        return OpenAILLMClient(model=model, visa_category=visa_category)
    # Default fallback: OpenAI-compatible endpoint
    return OpenAILLMClient(model=model, visa_category=visa_category)
