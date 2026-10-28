"""widen the ACTIVE-rule uniqueness index to include category

uq_rules_active_per_field previously enforced "at most one ACTIVE rule per
(program_id, field)". That is narrower than the invariant it's meant to
protect: EnhancedRuleRepo.activate_rule's own docstring and
_find_active_for_logical_key() define the logical key as
(program_id, category, field) - a rule in a different category that happens
to target the same field is a different logical rule and must be allowed to
be ACTIVE at the same time.

Found by driving a real requirement through approve -> compile -> activate
(SKILLED_189) and then testing the DB constraint directly: a same-field,
different-category ACTIVE insert was incorrectly rejected with
"duplicate key value violates unique constraint uq_rules_active_per_field",
even though the application layer explicitly does not consider that a
conflict.

Revision ID: 7c9e2a4f1b6d
Revises: a3c5e7f9b1d3
Create Date: 2026-10-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7c9e2a4f1b6d'
down_revision: Union[str, Sequence[str], None] = 'a3c5e7f9b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = "uq_rules_active_per_field"


def upgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="rules")
    op.create_index(
        _INDEX_NAME, "rules", ["program_id", "category", "field"],
        unique=True, postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="rules")
    op.create_index(
        _INDEX_NAME, "rules", ["program_id", "field"],
        unique=True, postgresql_where=sa.text("status = 'ACTIVE'"),
    )
