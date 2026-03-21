"""Add incident_clusters, incident_feedback tables and missing incidents columns.

Revision ID: 20260319_0005
Revises: 20260318_0004
Create Date: 2026-03-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260319_0005"
down_revision = "20260318_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. incident_clusters ─────────────────────────────────────────────────
    op.create_table(
        "incident_clusters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'open'")),
        sa.Column("root_cause_summary", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=False, server_default=sa.text("'warning'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_incident_clusters_status", "incident_clusters", ["status"])
    op.create_index("ix_incident_clusters_severity", "incident_clusters", ["severity"])
    op.create_index("ix_incident_clusters_created_at", "incident_clusters", ["created_at"])

    # ── 2. incident_feedback ─────────────────────────────────────────────────
    op.create_table(
        "incident_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("was_false_positive", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "resolution_effectiveness",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
        sa.Column("operator_notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=False, server_default=sa.text("'operator'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_incident_feedback_incident_id", "incident_feedback", ["incident_id"])
    op.create_index("ix_incident_feedback_created_by", "incident_feedback", ["created_by"])
    op.create_index("ix_incident_feedback_created_at", "incident_feedback", ["created_at"])

    # ── 3. incidents.incident_no ─────────────────────────────────────────────
    op.add_column(
        "incidents",
        sa.Column("incident_no", sa.String(length=32), nullable=True),
    )
    op.create_index("ix_incidents_incident_no", "incidents", ["incident_no"], unique=True)

    # ── 4. incidents.incident_cluster_id ─────────────────────────────────────
    op.add_column(
        "incidents",
        sa.Column(
            "incident_cluster_id",
            sa.Integer(),
            sa.ForeignKey("incident_clusters.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_incidents_incident_cluster_id", "incidents", ["incident_cluster_id"])


def downgrade() -> None:
    op.drop_index("ix_incidents_incident_cluster_id", table_name="incidents")
    op.drop_column("incidents", "incident_cluster_id")

    op.drop_index("ix_incidents_incident_no", table_name="incidents")
    op.drop_column("incidents", "incident_no")

    op.drop_index("ix_incident_feedback_created_at", table_name="incident_feedback")
    op.drop_index("ix_incident_feedback_created_by", table_name="incident_feedback")
    op.drop_index("ix_incident_feedback_incident_id", table_name="incident_feedback")
    op.drop_table("incident_feedback")

    op.drop_index("ix_incident_clusters_created_at", table_name="incident_clusters")
    op.drop_index("ix_incident_clusters_severity", table_name="incident_clusters")
    op.drop_index("ix_incident_clusters_status", table_name="incident_clusters")
    op.drop_table("incident_clusters")
