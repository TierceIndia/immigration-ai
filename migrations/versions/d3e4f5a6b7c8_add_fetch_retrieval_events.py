"""add retrieval-event detail to fetch_log

An immutable snapshot is only auditable if the acquisition attempt is recorded:
which transport ran, where it ended after redirects, the content type, and where
time went (limiter, browser launch, navigation, selector wait). The sidecar
manifest remains the evidence beside the bytes; these columns make the same
facts queryable for operations and reconciliation.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-10-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3e4f5a6b7c8'
down_revision: Union[str, Sequence[str], None] = 'c2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('fetch_log', sa.Column('retrieval_id', sa.String(36), nullable=True))
    op.create_index(
        'uq_fetch_log_retrieval_id', 'fetch_log', ['retrieval_id'], unique=True,
    )
    op.add_column('fetch_log', sa.Column('acquisition', sa.String(32), nullable=True))
    op.add_column('fetch_log', sa.Column('final_url', sa.String(2048), nullable=True))
    op.add_column('fetch_log', sa.Column('content_type', sa.String(128), nullable=True))
    op.add_column('fetch_log', sa.Column('limiter_wait_ms', sa.Integer(), nullable=True))
    op.add_column('fetch_log', sa.Column('browser_launch_ms', sa.Integer(), nullable=True))
    op.add_column('fetch_log', sa.Column('goto_ms', sa.Integer(), nullable=True))
    op.add_column('fetch_log', sa.Column('selector_wait_ms', sa.Integer(), nullable=True))
    op.add_column('fetch_log', sa.Column('selector_timed_out', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('fetch_log', 'selector_timed_out')
    op.drop_column('fetch_log', 'selector_wait_ms')
    op.drop_column('fetch_log', 'goto_ms')
    op.drop_column('fetch_log', 'browser_launch_ms')
    op.drop_column('fetch_log', 'limiter_wait_ms')
    op.drop_column('fetch_log', 'content_type')
    op.drop_column('fetch_log', 'final_url')
    op.drop_column('fetch_log', 'acquisition')
    op.drop_index('uq_fetch_log_retrieval_id', table_name='fetch_log')
    op.drop_column('fetch_log', 'retrieval_id')
