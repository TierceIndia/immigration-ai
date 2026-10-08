"""add host_cooldowns — pause a host after it refuses us (403/429)

Records one row per hostname that has refused a fetch. The sync layer skips
sources on a cooling host, and the admin API reports 409 for a single-source
sync while the host is still paused.

Revision ID: b7c8d9e0f1a2
Revises: 0dbea9e27e35
Create Date: 2026-10-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, Sequence[str], None] = '0dbea9e27e35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'host_cooldowns',
        sa.Column('host', sa.String(length=255), nullable=False),
        sa.Column('cooldown_until', sa.DateTime(timezone=True), nullable=False),
        sa.Column('http_status', sa.Integer(), nullable=True),
        sa.Column('hits', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('source_id', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['source_id'], ['government_sources.source_id'],
            name='fk_host_cooldowns_source_id',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('host'),
    )
    op.create_index('idx_host_cooldown_until', 'host_cooldowns', ['cooldown_until'])


def downgrade() -> None:
    op.drop_index('idx_host_cooldown_until', table_name='host_cooldowns')
    op.drop_table('host_cooldowns')
