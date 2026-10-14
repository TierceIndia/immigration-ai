"""add stored_new to pipeline_runs (item 6 metrics - summaries/UI)

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-10-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b6c7d8e9f0a1'
down_revision: Union[str, Sequence[str], None] = 'a5b6c7d8e9f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('pipeline_runs',
        sa.Column('stored_new', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('pipeline_runs', 'stored_new')
