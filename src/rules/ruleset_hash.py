"""Ruleset fingerprinting.

A ruleset hash identifies *exactly* which rule versions a result was computed
from. Two assessments with the same hash were computed from the same
immutable rule versions; a different hash means a rule changed and the older
result cannot be compared to the newer one.

The fingerprint is a sha256 over the sorted ``rule_id:vN`` pairs, so it is
deterministic and independent of rule ordering.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


def compute_ruleset_hash(program_id: str, rules: Iterable[Any]) -> str:
    """Deterministic hash of the exact rule-version set used in a result.

    Accepts an iterable of ``(rule_id, version_number)`` tuples OR an iterable
    of ``rules.models.Rule`` dataclasses (both are used by callers). If any
    rule version changes, the hash changes.
    """
    def _fingerprints(items):
        for rule in items:
            if isinstance(rule, (tuple, list)):
                rid, ver = rule
            else:
                rid, ver = rule.rule_id, rule.version
            yield f"{rid}:v{ver}"

    rule_fingerprints = sorted(_fingerprints(rules))
    payload = json.dumps(
        {"program_id": program_id, "rules": rule_fingerprints},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
