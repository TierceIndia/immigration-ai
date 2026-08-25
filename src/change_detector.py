"""Basic (hash-equality) change detection.

Given a source's previously known hash (government_sources.last_hash) and a freshly
fetched hash, classify the outcome. No semantic diffing - that's explicitly out of
scope for Phase-1 (see DESIGN.md section 6 and section 8).
"""
from __future__ import annotations

from enum import Enum


class ChangeType(str, Enum):
    INITIAL = "INITIAL"      # no prior hash on record - first time we've seen this source
    CHANGED = "CHANGED"      # hash differs from last known hash
    UNCHANGED = "UNCHANGED"  # hash matches last known hash


def classify_change(previous_hash: str | None, new_hash: str) -> ChangeType:
    if previous_hash is None:
        return ChangeType.INITIAL
    if previous_hash != new_hash:
        return ChangeType.CHANGED
    return ChangeType.UNCHANGED
