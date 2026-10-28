"""add extract_requirements scope flag to government_sources

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-10-14

Default true (opt-out) so every existing source keeps extracting exactly as
before. The one deliberate exception: SRC-AU-MIGRATION-REGS-1994 covers the
whole Migration Regulations (all subclasses), most of which are out of
product scope - set to false here so it stays fetched/hashed/diffed/used as
evidence (fetch_document.py never reads this flag for that part) without
further requirement-extraction LLM spend. No rows are deleted.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, Sequence[str], None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('government_sources',
        sa.Column('extract_requirements', sa.Boolean(), nullable=False,
                   server_default=sa.true()))
    op.execute(
        "UPDATE government_sources SET extract_requirements = false "
        "WHERE source_id = 'SRC-AU-MIGRATION-REGS-1994'"
    )


def downgrade() -> None:
    op.drop_column('government_sources', 'extract_requirements')
