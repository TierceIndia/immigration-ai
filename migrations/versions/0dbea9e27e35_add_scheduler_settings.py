"""Add scheduler_settings — a single-row, admin-editable override of the
government sync schedule (daily cron time or interval), so a change made via
PUT /admin/scheduler survives a process restart instead of reverting to the
GOVERNMENT_SYNC_*_UTC / GOVERNMENT_SYNC_INTERVAL_MINUTES env-var defaults.

Revision ID: 0dbea9e27e35
Revises: 112f70d87c01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0dbea9e27e35'
down_revision: Union[str, Sequence[str], None] = '112f70d87c01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = :name"
        ),
        {"name": name},
    ).fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "scheduler_settings"):
        return

    op.create_table(
        "scheduler_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("mode", sa.String(16), nullable=False, server_default="daily"),
        sa.Column("hour_utc", sa.Integer(), nullable=True),
        sa.Column("minute_utc", sa.Integer(), nullable=True),
        sa.Column("interval_minutes", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 1", name="ck_scheduler_settings_singleton"),
        sa.CheckConstraint("mode IN ('daily', 'interval')", name="ck_scheduler_settings_mode"),
        sa.CheckConstraint(
            "hour_utc IS NULL OR (hour_utc >= 0 AND hour_utc <= 23)",
            name="ck_scheduler_settings_hour",
        ),
        sa.CheckConstraint(
            "minute_utc IS NULL OR (minute_utc >= 0 AND minute_utc <= 59)",
            name="ck_scheduler_settings_minute",
        ),
        sa.CheckConstraint(
            "interval_minutes IS NULL OR interval_minutes >= 15",
            name="ck_scheduler_settings_interval",
        ),
    )


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "scheduler_settings"):
        op.drop_table("scheduler_settings")
