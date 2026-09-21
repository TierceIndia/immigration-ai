"""add explicit fetch-failure classification (error_type)

Adds machine-readable failure-class columns so DNS resolution failures, timeouts,
TLS errors, etc. are persisted (currently they all collapse into FETCH_FAILED
with only a human-readable error string).

Revision ID: e4f6a1b2c3d4
Revises: f8a9b0c1d2e3
Create Date: 2026-09-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e4f6a1b2c3d4'
down_revision: Union[str, Sequence[str], None] = 'f8a9b0c1d2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('government_sources',
        sa.Column('last_error_type', sa.String(length=64), nullable=True))
    op.add_column('fetch_log',
        sa.Column('error_type', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('fetch_log', 'error_type')
    op.drop_column('government_sources', 'last_error_type')