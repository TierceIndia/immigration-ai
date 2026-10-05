"""Orchestration: document_chunks -> candidate chunks -> LLM extraction -> schema validation ->
evidence validation -> normalization. Returns a list of ExtractionOutcome, one per candidate chunk
processed, with full diagnostics for the run report. Persistence (requirement_repository) is
deliberately a separate step, called by run_extraction.py, so this module has no DB write side
effects and is trivially unit-testable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from chunk_selector import build_context_window, select_candidates
from evidence_validator import validate_evidence
from llm_client import PROMPT_VERSION, LLMClient
from requirement_normalizer import normalize_requirement
from requirement_validator import parse_llm_output, validate_logic, validate_schema


@dataclass
class ExtractionOutcome:
    candidate_chunk_id: int
    llm_called: bool
    raw_llm_output: str | None = None
    schema_errors: list[str] = field(default_factory=list)
    accepted_requirements: list[dict] = field(default_factory=list)  # normalized, evidence-verified, ready to persist
    rejected_requirements: list[dict] = field(default_factory=list)  # {requirement, reasons}
    needs_review_requirements: list[dict] = field(default_factory=list)  # {requirement, reasons}


def extract_from_document(
    *, source: dict, document: dict, all_chunks: list[dict], llm_client: LLMClient,
    context_window: int = 1,
    on_outcome=None,
) -> list[ExtractionOutcome]:
    """all_chunks: list of dicts (chunk_id, chunk_type, heading_path, order_index, raw_text, page_number)
    for the CURRENT version of this document only.
    """
    candidates = select_candidates(all_chunks)
    outcomes: list[ExtractionOutcome] = []

    for candidate in candidates:
        context_chunks = build_context_window(all_chunks, candidate, window=context_window)
        supplied_ids = {candidate.chunk_id} | {c["chunk_id"] for c in context_chunks}
        supplied_chunks_by_id = {candidate.chunk_id: candidate.raw_text}
        supplied_chunks_by_id.update({c["chunk_id"]: c["raw_text"] for c in context_chunks})

        input_context = {
            "source": source,
            "document": document,
            "primary_chunk": {
                "chunk_id": candidate.chunk_id, "chunk_type": candidate.chunk_type,
                "heading_path": candidate.heading_path, "page_number": candidate.page_number,
                "raw_text": candidate.raw_text,
            },
            "context_chunks": [
                {"chunk_id": c["chunk_id"], "chunk_type": c["chunk_type"],
                 "heading_path": c.get("heading_path"), "page_number": c.get("page_number"),
                 "raw_text": c["raw_text"]}
                for c in context_chunks
            ],
        }

        outcome = ExtractionOutcome(candidate_chunk_id=candidate.chunk_id, llm_called=True)
        seen_semantics: dict[tuple, dict] = {}
        raw_output = llm_client.extract(input_context)
        outcome.raw_llm_output = raw_output

        parsed, parse_errors = parse_llm_output(raw_output)
        if parsed is None:
            outcome.schema_errors = parse_errors
            outcomes.append(outcome)
            continue

        schema_result = validate_schema(parsed)
        if not schema_result.ok:
            outcome.schema_errors = schema_result.errors
            outcomes.append(outcome)
            continue

        for req in parsed.get("requirements", []):
            logic_result = validate_logic(req)
            evidence_result = validate_evidence(req.get("evidence", []), supplied_chunks_by_id)

            reasons = list(logic_result.errors) + list(evidence_result.errors)

            if not evidence_result.ok:
                # fabricated/unverifiable evidence is always a hard reject - never "needs review"
                outcome.rejected_requirements.append({"requirement": req, "reasons": evidence_result.errors})
                continue

            if not logic_result.ok:
                outcome.rejected_requirements.append({"requirement": req, "reasons": logic_result.errors})
                continue

            norm_result = normalize_requirement(req)
            normalized = norm_result.normalized

            semantic_key = (
                normalized.get("category"), normalized.get("field"),
                normalized.get("operator"),
                json.dumps(normalized.get("value"), sort_keys=True, ensure_ascii=False),
                normalized.get("unit"), normalized.get("mandatory"),
            )
            existing = seen_semantics.get(semantic_key)
            if existing is not None:
                existing_evidence = {
                    (e.get("chunk_id"), e.get("evidence_text"), e.get("evidence_type"))
                    for e in existing.get("evidence", [])
                }
                existing.setdefault("evidence", []).extend(
                    e for e in normalized.get("evidence", [])
                    if (e.get("chunk_id"), e.get("evidence_text"), e.get("evidence_type"))
                    not in existing_evidence
                )
                continue
            seen_semantics[semantic_key] = normalized

            if norm_result.warnings:
                outcome.needs_review_requirements.append({
                    "requirement": normalized, "reasons": norm_result.warnings + logic_result.warnings,
                })
            else:
                outcome.accepted_requirements.append(normalized)

        outcomes.append(outcome)
        # Stream each completed candidate to the caller so it can be persisted
        # durably as it is produced. Without this, a provider failure part-way
        # through a long document (quota exhaustion, rate limit) discards every
        # LLM call already made — a real loss observed against the live
        # Migration Regulations 1994 extraction.
        if on_outcome is not None:
            on_outcome(outcome)

    return outcomes
