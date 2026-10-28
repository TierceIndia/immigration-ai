"""add content_kind to government_sources (PAGE | NEWS_LIST)

Structure, not transport: source_type (HTML/PDF) decides how the bytes are
fetched, content_kind decides how they are chunked. Existing rows are PAGE,
which is exactly today's behaviour.

Revision ID: c7d8e9f0a1b3
Revises: b6c7d8e9f0a1
Create Date: 2026-10-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7d8e9f0a1b3'
down_revision: Union[str, Sequence[str], None] = 'b6c7d8e9f0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'government_sources',
        sa.Column('content_kind', sa.String(length=16), nullable=False,
                  server_default='PAGE'),
    )
    op.create_check_constraint(
        'ck_sources_content_kind',
        'government_sources',
        "content_kind IN ('PAGE', 'NEWS_LIST')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_sources_content_kind', 'government_sources', type_='check')
    op.drop_column('government_sources', 'content_kind')
