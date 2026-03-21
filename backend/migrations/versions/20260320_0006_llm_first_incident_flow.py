"""Add llm_analyses and incident analyzer fields.

Revision ID: 20260320_0006
Revises: 20260319_0005
Create Date: 2026-03-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260320_0006"
down_revision = "20260319_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id"), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False, server_default=sa.text("'no_issue'")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'completed'")),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_log_ids_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("open_incident_ids_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("provider", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("prompt_version", sa.String(length=80), nullable=False, server_default=sa.text("'v1'")),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_llm_analyses_incident_id", "llm_analyses", ["incident_id"])
    op.create_index("ix_llm_analyses_decision", "llm_analyses", ["decision"])
    op.create_index("ix_llm_analyses_status", "llm_analyses", ["status"])
    op.create_index("ix_llm_analyses_window_start", "llm_analyses", ["window_start"])
    op.create_index("ix_llm_analyses_window_end", "llm_analyses", ["window_end"])
    op.create_index("ix_llm_analyses_created_at", "llm_analyses", ["created_at"])

    op.add_column("incidents", sa.Column("probable_root_cause", sa.Text(), nullable=True))
    op.add_column(
        "incidents",
        sa.Column("affected_scope_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "incidents",
        sa.Column("confidence_score", sa.Integer(), nullable=False, server_default=sa.text("50")),
    )
    op.add_column(
        "incidents",
        sa.Column("last_analysis_id", sa.Integer(), sa.ForeignKey("llm_analyses.id"), nullable=True),
    )
    op.create_index("ix_incidents_last_analysis_id", "incidents", ["last_analysis_id"])


def downgrade() -> None:
    op.drop_index("ix_incidents_last_analysis_id", table_name="incidents")
    op.drop_column("incidents", "last_analysis_id")
    op.drop_column("incidents", "confidence_score")
    op.drop_column("incidents", "affected_scope_json")
    op.drop_column("incidents", "probable_root_cause")

    op.drop_index("ix_llm_analyses_created_at", table_name="llm_analyses")
    op.drop_index("ix_llm_analyses_window_end", table_name="llm_analyses")
    op.drop_index("ix_llm_analyses_window_start", table_name="llm_analyses")
    op.drop_index("ix_llm_analyses_status", table_name="llm_analyses")
    op.drop_index("ix_llm_analyses_decision", table_name="llm_analyses")
    op.drop_index("ix_llm_analyses_incident_id", table_name="llm_analyses")
    op.drop_table("llm_analyses")
