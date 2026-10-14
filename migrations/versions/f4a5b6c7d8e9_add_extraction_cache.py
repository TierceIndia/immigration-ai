"""add extraction_cache (persistent chunk-level LLM extraction cache)

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-10-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, Sequence[str], None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'extraction_cache',
        sa.Column('chunk_content_hash', sa.String(64), primary_key=True),
        sa.Column('prompt_version', sa.String(64), primary_key=True),
        sa.Column('model', sa.String(64), primary_key=True),
        sa.Column('source_chunk_id', sa.Integer(), nullable=False),
        sa.Column('raw_output', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('extraction_cache')
