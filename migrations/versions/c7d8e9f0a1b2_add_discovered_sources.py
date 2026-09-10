"""add discovered_sources table (source auto-discovery candidates)

Revision ID: c7d8e9f0a1b2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'discovered_sources',
        sa.Column('discovered_source_id', sa.String(length=32), nullable=False),
        sa.Column('seed_url', sa.Text(), nullable=False),
        sa.Column('candidate_url', sa.Text(), nullable=False),
        sa.Column('page_title', sa.String(length=512), nullable=True),
        sa.Column('matched_keywords', sa.JSON(), nullable=False),
        sa.Column('confidence_score', sa.Integer(), nullable=False),
        sa.Column('suggested_visa_category', sa.String(length=32), nullable=True),
        sa.Column('suggested_country_id', sa.String(length=8), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('discovered_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('discovery_run_id', sa.String(length=36), nullable=True),
        sa.Column('reviewed_by', sa.String(length=36), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('discovered_source_id'),
        sa.UniqueConstraint('candidate_url'),
    )
    op.create_index('idx_discovered_status', 'discovered_sources', ['status'], unique=False)
    op.create_index('idx_discovered_category', 'discovered_sources', ['suggested_visa_category'], unique=False)
    op.create_index('idx_discovered_run', 'discovered_sources', ['discovery_run_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_discovered_run', table_name='discovered_sources')
    op.drop_index('idx_discovered_category', table_name='discovered_sources')
    op.drop_index('idx_discovered_status', table_name='discovered_sources')
    op.drop_table('discovered_sources')