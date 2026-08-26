"""Tests for rule activation status machine."""
from __future__ import annotations

import pytest
from phase3.rule_activation import (
    can_transition, validate_transition, RuleLifecycleStatus,
    _VALID_TRANSITIONS,
)


class TestStatusTransitions:
    def test_draft_to_validated(self):
        assert can_transition("DRAFT", "VALIDATED") is True

    def test_draft_to_rejected(self):
        assert can_transition("DRAFT", "REJECTED") is True

    def test_draft_to_active_blocked(self):
        assert can_transition("DRAFT", "ACTIVE") is False

    def test_draft_to_approved_blocked(self):
        assert can_transition("DRAFT", "APPROVED") is False

    def test_validated_to_approved(self):
        assert can_transition("VALIDATED", "APPROVED") is True

    def test_validated_to_rejected(self):
        assert can_transition("VALIDATED", "REJECTED") is True

    def test_validated_to_draft(self):
        assert can_transition("VALIDATED", "DRAFT") is True

    def test_approved_to_active(self):
        assert can_transition("APPROVED", "ACTIVE") is True

    def test_approved_to_rejected(self):
        assert can_transition("APPROVED", "REJECTED") is True

    def test_active_to_superseded(self):
        assert can_transition("ACTIVE", "SUPERSEDED") is True

    def test_active_to_retired(self):
        assert can_transition("ACTIVE", "RETIRED") is True

    def test_superseded_to_retired(self):
        assert can_transition("SUPERSEDED", "RETIRED") is True

    def test_retired_is_terminal(self):
        assert can_transition("RETIRED", "ACTIVE") is False
        assert can_transition("RETIRED", "DRAFT") is False

    def test_rejected_to_draft(self):
        assert can_transition("REJECTED", "DRAFT") is True


class TestValidateTransition:
    def test_valid_transition_no_error(self):
        validate_transition("DRAFT", "VALIDATED")

    def test_invalid_transition_raises(self):
        with pytest.raises(ValueError, match="Invalid rule status transition"):
            validate_transition("DRAFT", "ACTIVE")

    def test_error_message_lists_allowed(self):
        with pytest.raises(ValueError, match="VALIDATED"):
            validate_transition("DRAFT", "ACTIVE")


class TestAllStatuses:
    def test_all_statuses_are_string_enum(self):
        for s in RuleLifecycleStatus:
            assert isinstance(s.value, str)
            assert len(s.value) > 0

    def test_transition_table_complete(self):
        for s in RuleLifecycleStatus:
            assert s.value in _VALID_TRANSITIONS
