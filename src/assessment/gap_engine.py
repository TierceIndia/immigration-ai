"""Phase 3C-4: Gap Engine — identify missing information and prioritize gaps.

When assessment returns INCOMPLETE, the Gap Engine:
1. Finds all UNKNOWN rule evaluations
2. Determines which fields are missing
3. Deduplicates missing fields (multiple rules may reference same field)
4. Prioritizes gaps (CRITICAL > HIGH > MEDIUM > LOW)
5. Links each gap to its source rule(s) and evidence
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from rules.models import Rule, RuleEvaluation, EvaluationStatus


class GapPriority(str, Enum):
    CRITICAL = "CRITICAL"  # critical=True rule
    HIGH = "HIGH"          # mandatory=True, critical=False
    MEDIUM = "MEDIUM"      # mandatory=False, critical=False
    LOW = "LOW"            # optional/informational


@dataclass
class Gap:
    """A missing piece of information needed for eligibility assessment."""
    field: str
    priority: GapPriority
    source_rule_ids: list[str]
    source_evidence: str | None
    question: str


# Deterministic field → question mapping
_FIELD_QUESTIONS: dict[str, str] = {
    "age": "What is your age?",
    "english_level": "What is your current English proficiency level? (none/vocational/competent/proficient/superior)",
    "points": "How many points have you calculated on the Points Test?",
    "occupation": "What is your occupation ANZSCO code?",
    "skills_assessment_status": "What is the status of your skills assessment? (NOT_APPLICABLE/PENDING/VALID/INVALID/EXEMPT)",
    "health_status": "Have you completed your health examination? (UNKNOWN/PASSED/FAILED)",
    "character_status": "Have you completed your character assessment? (UNKNOWN/PASSED/FAILED)",
    "has_regional_nomination": "Do you have a regional nomination?",
    "visa_subclass_detail": "What visa subclass are you applying for?",
}


class GapEngine:
    """Identify and prioritize gaps in applicant data."""

    @staticmethod
    def find_gaps(
        evaluations: list[RuleEvaluation],
        rules_by_id: dict[str, Rule],
    ) -> list[Gap]:
        """Find all UNKNOWN evaluations and produce prioritized gaps.

        Deduplicates by field: if two rules reference the same missing field,
        they are combined into a single gap with multiple source rules.
        """
        unknown_by_field: dict[str, list[RuleEvaluation]] = {}

        for ev in evaluations:
            if ev.status == EvaluationStatus.UNKNOWN:
                unknown_by_field.setdefault(ev.field, []).append(ev)

        gaps: list[Gap] = []
        for field_name, evals in unknown_by_field.items():
            source_rule_ids = list({e.rule_id for e in evals})

            # Find the highest-priority source rule
            source_rules = [
                rules_by_id[rid] for rid in source_rule_ids if rid in rules_by_id
            ]
            priority = _determine_priority(source_rules)

            # Get evidence from the first source rule's provenance
            evidence = None
            for r in source_rules:
                if r.provenance.evidence_text:
                    evidence = r.provenance.evidence_text
                    break

            question = _FIELD_QUESTIONS.get(
                field_name,
                f"Please provide your {field_name.replace('_', ' ')}.",
            )

            gaps.append(Gap(
                field=field_name,
                priority=priority,
                source_rule_ids=sorted(source_rule_ids),
                source_evidence=evidence,
                question=question,
            ))

        # Sort by priority
        gaps.sort(key=lambda g: list(GapPriority).index(g.priority))
        return gaps


def _determine_priority(rules: list[Rule]) -> GapPriority:
    """Determine gap priority from source rules.

    Highest priority among all source rules wins.
    """
    if any(r.critical for r in rules):
        return GapPriority.CRITICAL
    if any(r.mandatory for r in rules):
        return GapPriority.HIGH
    return GapPriority.MEDIUM
