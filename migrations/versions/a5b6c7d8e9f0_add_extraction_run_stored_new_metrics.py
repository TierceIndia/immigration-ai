"""add stored_new/merged_into_existing to extraction_runs (item 6 metrics)

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-10-14

requirements_extracted counts attempts (every accepted/needs-review
candidate persist_requirement() was called for), not stored rows - a dedup/
conflict/evidence-merge can turn several attempts into zero new rows. These
two columns split that apart so "how much new knowledge did this run add"
(stored_new) is a real, queryable number, not something only requirements_extracted
could (mis)answer.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a5b6c7d8e9f0'
down_revision: Union[str, Sequence[str], None] = 'f4a5b6c7d8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('extraction_runs',
        sa.Column('stored_new', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('extraction_runs',
        sa.Column('merged_into_existing', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('extraction_runs', 'merged_into_existing')
    op.drop_column('extraction_runs', 'stored_new')
