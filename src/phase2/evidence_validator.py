"""Evidence validation - the single most important guardrail in Phase 2.

An LLM must never be trusted to have quoted the source correctly. This module independently
verifies, for every evidence entry an LLM returns:
  1. the cited chunk_id was actually one of the chunk_ids supplied to it (no invented chunk IDs)
  2. the cited evidence_text is an exact substring of that chunk's raw_text (no fabricated quotes)

Whitespace is normalized before comparison (collapsing runs of whitespace) because HTML/PDF
extraction can introduce minor whitespace differences that don't change the meaning of the text,
but the *words* must match exactly - no paraphrase is accepted as evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


@dataclass
class EvidenceValidationResult:
    ok: bool
    errors: list[str]


def validate_evidence(evidence_list: list[dict], supplied_chunks_by_id: dict[int, str]) -> EvidenceValidationResult:
    """evidence_list: [{"chunk_id":.., "evidence_text":.., "evidence_type":..}, ...]
    supplied_chunks_by_id: {chunk_id: raw_text} for every chunk actually given to the LLM in this call.
    """
    errors: list[str] = []

    if not evidence_list:
        return EvidenceValidationResult(ok=False, errors=["no evidence provided"])

    for ev in evidence_list:
        chunk_id = ev.get("chunk_id")
        evidence_text = ev.get("evidence_text", "")

        if chunk_id not in supplied_chunks_by_id:
            errors.append(
                f"evidence cites chunk_id={chunk_id!r} which was not supplied to the model "
                f"(hallucinated provenance) - supplied ids were {sorted(supplied_chunks_by_id)}"
            )
            continue

        chunk_text_norm = _normalize(supplied_chunks_by_id[chunk_id])
        evidence_norm = _normalize(evidence_text)

        if not evidence_norm:
            errors.append(f"empty evidence_text for chunk_id={chunk_id}")
            continue

        if evidence_norm not in chunk_text_norm:
            errors.append(
                f"evidence_text for chunk_id={chunk_id} is not a verbatim substring of the "
                f"source chunk (fabricated/paraphrased quote): {evidence_text[:120]!r}"
            )

    return EvidenceValidationResult(ok=not errors, errors=errors)
