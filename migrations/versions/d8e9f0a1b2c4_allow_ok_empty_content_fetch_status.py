"""allow fetch_status = OK_EMPTY_CONTENT on government_sources

A fetch that worked and a parse that found nothing (a scanned PDF, or a page
that renders no structural content) is neither a failure nor an ordinary
success. Calling it "OK" hid sources that were quietly contributing no
knowledge, so it gets its own value - which the fetch_status CHECK constraint
has to allow.

Revision ID: d8e9f0a1b2c4
Revises: c7d8e9f0a1b3
Create Date: 2026-10-19

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd8e9f0a1b2c4'
down_revision: Union[str, Sequence[str], None] = 'c7d8e9f0a1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = (
    "fetch_status IS NULL OR fetch_status IN "
    "('OK','FETCH_FAILED','ACCESS_BLOCKED','NETWORK_RESTRICTED',"
    "'REQUIRES_BROWSER','REQUIRES_MANUAL_DOWNLOAD','INVALID_CONTENT')"
)
_NEW = (
    "fetch_status IS NULL OR fetch_status IN "
    "('OK','OK_EMPTY_CONTENT','FETCH_FAILED','ACCESS_BLOCKED',"
    "'NETWORK_RESTRICTED','REQUIRES_BROWSER','REQUIRES_MANUAL_DOWNLOAD',"
    "'INVALID_CONTENT')"
)


def upgrade() -> None:
    op.drop_constraint('ck_source_fetch_status', 'government_sources', type_='check')
    op.create_check_constraint(
        'ck_source_fetch_status', 'government_sources', _NEW,
    )


def downgrade() -> None:
    # Any row already carrying the new value would violate the old constraint;
    # fold it back to the closest truthful older value first.
    op.execute(
        "UPDATE government_sources SET fetch_status = 'OK' "
        "WHERE fetch_status = 'OK_EMPTY_CONTENT'"
    )
    op.drop_constraint('ck_source_fetch_status', 'government_sources', type_='check')
    op.create_check_constraint(
        'ck_source_fetch_status', 'government_sources', _OLD,
    )
