"""widen requirement_id (and derived rule_id) columns to prevent silent truncation

Requirement.requirement_id was String(48): "REQ-{program_id}-{sem_key}-{uuid6}"
(the CONFLICT-path format) can legitimately exceed that once program_id is long
(e.g. "LEGISLATION_MIGRATION_REGS", 27 chars) — confirmed in production on
PostgreSQL, which raises StringDataRightTruncation and silently drops the
insert (SQLite does not enforce VARCHAR length at all, so this never surfaced
locally). Widened to 96 chars, with matching width on every column that stores
or foreign-keys to a requirement_id.

rules.rule_id is also widened: requirement_to_rule.py builds
rule_id = f"RULE-{requirement_id}" directly from requirement_id, so leaving
rule_id at its old width would just relocate the identical truncation failure
from requirement insertion to rule compilation for the same long-program_id
content. rules.requirement_id (the FK column) is widened for the same reason
as every other requirement_id-referencing column below. And every column that
itself stores or foreign-keys to rule_id (rule_versions.rule_id,
assessment_rule_results.rule_id, gaps.rule_id) is widened for the identical
cascading reason.

Revision ID: a3c5e7f9b1d3
Revises: e4f6a1b2c3d4
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3c5e7f9b1d3'
down_revision: Union[str, Sequence[str], None] = 'e4f6a1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_LENGTH = 48
_NEW_LENGTH = 96

# (table, column, nullable) triples. Increasing a VARCHAR's length is a
# metadata-only change on PostgreSQL (no table rewrite, no lock beyond the
# brief DDL lock).
_COLUMNS = [
    ("requirements", "requirement_id", False),
    ("requirement_evidence", "requirement_id", False),
    ("requirement_versions", "requirement_id", False),
    ("requirement_review", "requirement_id", False),
    ("requirement_references", "requirement_id", False),
    ("rules", "requirement_id", False),
    ("rules", "rule_id", False),
    ("rule_versions", "rule_id", False),
    ("assessment_rule_results", "rule_id", False),
    ("gaps", "rule_id", True),
]


def upgrade() -> None:
    for table, column, nullable in _COLUMNS:
        op.alter_column(
            table, column,
            existing_type=sa.String(length=_OLD_LENGTH),
            type_=sa.String(length=_NEW_LENGTH),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    for table, column, nullable in reversed(_COLUMNS):
        op.alter_column(
            table, column,
            existing_type=sa.String(length=_NEW_LENGTH),
            type_=sa.String(length=_OLD_LENGTH),
            existing_nullable=nullable,
        )
