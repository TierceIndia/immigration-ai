"""add the first batch of modelled applicant concepts

Revision ID: b1c2d3e4f5a6
Revises: d8e9f0a1b2c4
Create Date: 2026-10-22

Six applicant facts that previously had no place to live. Before this, a
requirement about visa history, sponsorship, work-experience duration,
being in Australia at application, health insurance, or the genuine
temporary entrant test could only ever be stored as NOT_MODELED - it could
never resolve to PASS or FAIL, no matter what an applicant supplied.

All six columns are nullable with no server default: a missing fact stays
missing (UNKNOWN), which is the whole point of the rule engine's contract.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'd8e9f0a1b2c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('applicant_profiles', sa.Column('visa_history', sa.String(32), nullable=True))
    op.add_column('applicant_profiles', sa.Column('employer_sponsored', sa.Boolean(), nullable=True))
    op.add_column('applicant_profiles', sa.Column('work_experience_years', sa.Integer(), nullable=True))
    op.add_column('applicant_profiles',
                  sa.Column('in_australia_at_application', sa.Boolean(), nullable=True))
    op.add_column('applicant_profiles', sa.Column('has_health_insurance', sa.Boolean(), nullable=True))
    op.add_column('applicant_profiles',
                  sa.Column('genuine_temporary_entrant', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('applicant_profiles', 'genuine_temporary_entrant')
    op.drop_column('applicant_profiles', 'has_health_insurance')
    op.drop_column('applicant_profiles', 'in_australia_at_application')
    op.drop_column('applicant_profiles', 'work_experience_years')
    op.drop_column('applicant_profiles', 'employer_sponsored')
    op.drop_column('applicant_profiles', 'visa_history')
