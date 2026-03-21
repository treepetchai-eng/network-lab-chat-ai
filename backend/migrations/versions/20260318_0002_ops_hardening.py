"""Ops hardening: approval policy metadata, audit trail, and AI artifacts.

Revision ID: 20260318_0002
Revises: 20260318_0001
Create Date: 2026-03-18 11:25:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260318_0002"
down_revision = "20260318_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "approvals",
        sa.Column("requested_by_role", sa.String(length=32), nullable=False, server_default=sa.text("'operator'")),
    )
    op.add_column(
        "approvals",
        sa.Column("reviewed_by_role", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "approvals",
        sa.Column("executed_by", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "approvals",
        sa.Column("executed_by_role", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "approvals",
        sa.Column("action_id", sa.String(length=80), nullable=False, server_default=sa.text("'generic_config_change'")),
    )
    op.add_column(
        "approvals",
        sa.Column("decision_comment", sa.Text(), nullable=True),
    )
    op.add_column(
        "approvals",
        sa.Column("required_approval_role", sa.String(length=32), nullable=False, server_default=sa.text("'approver'")),
    )
    op.add_column(
        "approvals",
        sa.Column("required_execution_role", sa.String(length=32), nullable=False, server_default=sa.text("'operator'")),
    )
    op.add_column(
        "approvals",
        sa.Column("readiness", sa.String(length=48), nullable=False, server_default=sa.text("'ready_for_human_review'")),
    )
    op.add_column(
        "approvals",
        sa.Column("readiness_score", sa.Integer(), nullable=False, server_default=sa.text("50")),
    )
    op.add_column(
        "approvals",
        sa.Column("execution_status", sa.String(length=48), nullable=False, server_default=sa.text("'awaiting_approval'")),
    )
    op.add_column(
        "approvals",
        sa.Column("failure_category", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "approvals",
        sa.Column("policy_snapshot_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "approvals",
        sa.Column("evidence_snapshot_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )

    op.create_index("ix_approvals_action_id", "approvals", ["action_id"])
    op.create_index("ix_approvals_execution_status", "approvals", ["execution_status"])
    op.create_index("ix_approvals_failure_category", "approvals", ["failure_category"])
    op.create_index("ix_approvals_readiness", "approvals", ["readiness"])

    op.execute(
        """
        UPDATE approvals
        SET execution_status = CASE
            WHEN status = 'executed' THEN 'succeeded'
            WHEN status = 'approved' THEN 'approved'
            WHEN status = 'rejected' THEN 'failed_blocked'
            ELSE 'awaiting_approval'
        END
        """
    )

    op.create_table(
        "audit_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor", sa.String(length=120), nullable=False, server_default=sa.text("'system'")),
        sa.Column("actor_role", sa.String(length=32), nullable=False, server_default=sa.text("'system'")),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=48), nullable=False, server_default=sa.text("'recorded'")),
        sa.Column("summary", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_entries_actor", "audit_entries", ["actor"])
    op.create_index("ix_audit_entries_actor_role", "audit_entries", ["actor_role"])
    op.create_index("ix_audit_entries_action", "audit_entries", ["action"])
    op.create_index("ix_audit_entries_created_at", "audit_entries", ["created_at"])
    op.create_index("ix_audit_entries_entity_id", "audit_entries", ["entity_id"])
    op.create_index("ix_audit_entries_entity_type", "audit_entries", ["entity_type"])
    op.create_index("ix_audit_entries_status", "audit_entries", ["status"])

    op.create_table(
        "ai_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("artifact_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=sa.text("''")),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id"), nullable=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id"), nullable=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("approval_id", sa.Integer(), sa.ForeignKey("approvals.id"), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("prompt_version", sa.String(length=80), nullable=False, server_default=sa.text("'v1'")),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("confidence_score", sa.Integer(), nullable=False, server_default=sa.text("50")),
        sa.Column("readiness", sa.String(length=48), nullable=False, server_default=sa.text("'informational'")),
        sa.Column("risk_explanation", sa.Text(), nullable=True),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("proposed_actions_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("content_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_artifacts_approval_id", "ai_artifacts", ["approval_id"])
    op.create_index("ix_ai_artifacts_artifact_type", "ai_artifacts", ["artifact_type"])
    op.create_index("ix_ai_artifacts_confidence_score", "ai_artifacts", ["confidence_score"])
    op.create_index("ix_ai_artifacts_created_at", "ai_artifacts", ["created_at"])
    op.create_index("ix_ai_artifacts_device_id", "ai_artifacts", ["device_id"])
    op.create_index("ix_ai_artifacts_incident_id", "ai_artifacts", ["incident_id"])
    op.create_index("ix_ai_artifacts_job_id", "ai_artifacts", ["job_id"])
    op.create_index("ix_ai_artifacts_readiness", "ai_artifacts", ["readiness"])


def downgrade() -> None:
    op.drop_index("ix_ai_artifacts_readiness", table_name="ai_artifacts")
    op.drop_index("ix_ai_artifacts_job_id", table_name="ai_artifacts")
    op.drop_index("ix_ai_artifacts_incident_id", table_name="ai_artifacts")
    op.drop_index("ix_ai_artifacts_device_id", table_name="ai_artifacts")
    op.drop_index("ix_ai_artifacts_created_at", table_name="ai_artifacts")
    op.drop_index("ix_ai_artifacts_confidence_score", table_name="ai_artifacts")
    op.drop_index("ix_ai_artifacts_artifact_type", table_name="ai_artifacts")
    op.drop_index("ix_ai_artifacts_approval_id", table_name="ai_artifacts")
    op.drop_table("ai_artifacts")

    op.drop_index("ix_audit_entries_status", table_name="audit_entries")
    op.drop_index("ix_audit_entries_entity_type", table_name="audit_entries")
    op.drop_index("ix_audit_entries_entity_id", table_name="audit_entries")
    op.drop_index("ix_audit_entries_created_at", table_name="audit_entries")
    op.drop_index("ix_audit_entries_action", table_name="audit_entries")
    op.drop_index("ix_audit_entries_actor_role", table_name="audit_entries")
    op.drop_index("ix_audit_entries_actor", table_name="audit_entries")
    op.drop_table("audit_entries")

    op.drop_index("ix_approvals_readiness", table_name="approvals")
    op.drop_index("ix_approvals_failure_category", table_name="approvals")
    op.drop_index("ix_approvals_execution_status", table_name="approvals")
    op.drop_index("ix_approvals_action_id", table_name="approvals")

    op.drop_column("approvals", "evidence_snapshot_json")
    op.drop_column("approvals", "policy_snapshot_json")
    op.drop_column("approvals", "failure_category")
    op.drop_column("approvals", "execution_status")
    op.drop_column("approvals", "readiness_score")
    op.drop_column("approvals", "readiness")
    op.drop_column("approvals", "required_execution_role")
    op.drop_column("approvals", "required_approval_role")
    op.drop_column("approvals", "decision_comment")
    op.drop_column("approvals", "action_id")
    op.drop_column("approvals", "executed_by_role")
    op.drop_column("approvals", "executed_by")
    op.drop_column("approvals", "reviewed_by_role")
    op.drop_column("approvals", "requested_by_role")
