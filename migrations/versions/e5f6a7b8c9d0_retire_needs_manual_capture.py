"""retire NEEDS_MANUAL_CAPTURE from government_sources.status

Manual capture could not prove provenance, and the status removed a source from
the scheduled sync set permanently. Existing rows are returned to ACTIVE so
automation keeps retrying them; the precise failure remains on fetch_status,
http_status, last_error and fetch_log.

Revision ID: e5f6a7b8c9d0
Revises: d3e4f5a6b7c8
Create Date: 2026-10-26

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = (
    "status IN ('ACTIVE','DISABLED','ACCESS_BLOCKED','NETWORK_RESTRICTED',"
    "'REQUIRES_BROWSER','REQUIRES_MANUAL_DOWNLOAD','NEEDS_MANUAL_CAPTURE','BROKEN')"
)
_NEW = (
    "status IN ('ACTIVE','DISABLED','ACCESS_BLOCKED','NETWORK_RESTRICTED',"
    "'REQUIRES_BROWSER','REQUIRES_MANUAL_DOWNLOAD','BROKEN')"
)


def upgrade() -> None:
    op.execute(
        "UPDATE government_sources SET status='ACTIVE' "
        "WHERE status='NEEDS_MANUAL_CAPTURE'"
    )
    with op.batch_alter_table('government_sources') as batch:
        batch.drop_constraint('ck_source_status', type_='check')
        batch.create_check_constraint('ck_source_status', _NEW)


def downgrade() -> None:
    with op.batch_alter_table('government_sources') as batch:
        batch.drop_constraint('ck_source_status', type_='check')
        batch.create_check_constraint('ck_source_status', _OLD)
