"""add last_successful_fetch_at to government_sources

Revision ID: a1b2c3d4e5f6
Revises: 49b9088f3a03
Create Date: 2026-08-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '49b9088f3a03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('government_sources',
        sa.Column('last_successful_fetch_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('government_sources', 'last_successful_fetch_at')