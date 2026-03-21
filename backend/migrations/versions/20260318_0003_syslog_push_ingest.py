"""Add raw log metadata for push-based syslog ingestion.

Revision ID: 20260318_0003
Revises: 20260318_0002
Create Date: 2026-03-18 13:45:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260318_0003"
down_revision = "20260318_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raw_logs",
        sa.Column("ingest_source", sa.String(length=32), nullable=False, server_default=sa.text("'remote_file'")),
    )
    op.add_column(
        "raw_logs",
        sa.Column("collector_name", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "raw_logs",
        sa.Column("event_uid", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_raw_logs_ingest_source", "raw_logs", ["ingest_source"])
    op.create_index("ix_raw_logs_collector_name", "raw_logs", ["collector_name"])
    op.create_index("ix_raw_logs_event_uid", "raw_logs", ["event_uid"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_raw_logs_event_uid", table_name="raw_logs")
    op.drop_index("ix_raw_logs_collector_name", table_name="raw_logs")
    op.drop_index("ix_raw_logs_ingest_source", table_name="raw_logs")
    op.drop_column("raw_logs", "event_uid")
    op.drop_column("raw_logs", "collector_name")
    op.drop_column("raw_logs", "ingest_source")
