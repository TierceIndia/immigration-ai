"""add etag/last_modified to government_sources for conditional GET

Revision ID: c1d2e3f4a5b6
Revises: b7c8d9e0f1a2
Create Date: 2026-10-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('government_sources',
        sa.Column('etag', sa.String(256), nullable=True))
    op.add_column('government_sources',
        sa.Column('last_modified', sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column('government_sources', 'last_modified')
    op.drop_column('government_sources', 'etag')
