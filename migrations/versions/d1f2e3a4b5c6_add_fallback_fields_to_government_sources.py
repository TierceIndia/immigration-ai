"""add fallback strategy fields to government_sources

Revision ID: d1f2e3a4b5c6
Revises: c7d8e9f0a1b2
Create Date: 2026-08-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1f2e3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('government_sources',
        sa.Column('fallback_source_id', sa.String(length=32), nullable=True))
    op.add_column('government_sources',
        sa.Column('fetch_status', sa.String(length=32), nullable=True))
    op.add_column('government_sources',
        sa.Column('http_status', sa.Integer(), nullable=True))
    op.add_column('government_sources',
        sa.Column('last_error', sa.Text(), nullable=True))
    op.add_column('government_sources',
        sa.Column('consecutive_failures', sa.Integer(), nullable=False, server_default='0'))
    op.create_foreign_key(
        'fk_government_sources_fallback',
        'government_sources', 'government_sources',
        ['fallback_source_id'], ['source_id']
    )


def downgrade() -> None:
    op.drop_constraint('fk_government_sources_fallback', 'government_sources', type_='foreignkey')
    op.drop_column('government_sources', 'consecutive_failures')
    op.drop_column('government_sources', 'last_error')
    op.drop_column('government_sources', 'http_status')
    op.drop_column('government_sources', 'fetch_status')
    op.drop_column('government_sources', 'fallback_source_id')