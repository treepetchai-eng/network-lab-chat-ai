"""Incident workflows, observed interfaces, remediation tasks, and scan history.

Revision ID: 20260318_0004
Revises: 20260318_0003
Create Date: 2026-03-18 18:45:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260318_0004"
down_revision = "20260318_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_interfaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("protocol", sa.String(length=80), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("last_state", sa.String(length=80), nullable=True),
        sa.Column("last_event_id", sa.Integer(), sa.ForeignKey("normalized_events.id"), nullable=True),
        sa.Column("last_event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", "name", name="uq_device_interfaces_device_name"),
    )
    op.create_index("ix_device_interfaces_device_id", "device_interfaces", ["device_id"])
    op.create_index("ix_device_interfaces_last_event_id", "device_interfaces", ["last_event_id"])
    op.create_index("ix_device_interfaces_last_event_time", "device_interfaces", ["last_event_time"])
    op.create_index("ix_device_interfaces_last_state", "device_interfaces", ["last_state"])
    op.create_index("ix_device_interfaces_name", "device_interfaces", ["name"])
    op.create_index("ix_device_interfaces_protocol", "device_interfaces", ["protocol"])

    op.add_column("incidents", sa.Column("assigned_to", sa.String(length=120), nullable=True))
    op.add_column("incidents", sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("incidents", sa.Column("acknowledged_by", sa.String(length=120), nullable=True))
    op.add_column("incidents", sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("incidents", sa.Column("resolved_by", sa.String(length=120), nullable=True))
    op.add_column("incidents", sa.Column("resolution_notes", sa.Text(), nullable=True))
    op.create_index("ix_incidents_assigned_to", "incidents", ["assigned_to"])

    op.execute("UPDATE incidents SET status = 'new' WHERE status = 'open'")
    op.execute("UPDATE incidents SET status = 'in_progress' WHERE status = 'investigating'")
    op.alter_column(
        "incidents",
        "status",
        existing_type=sa.String(length=32),
        server_default=sa.text("'new'"),
    )

    op.create_table(
        "incident_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False, server_default=sa.text("'system'")),
        sa.Column("actor_role", sa.String(length=32), nullable=False, server_default=sa.text("'system'")),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_incident_history_action", "incident_history", ["action"])
    op.create_index("ix_incident_history_actor", "incident_history", ["actor"])
    op.create_index("ix_incident_history_actor_role", "incident_history", ["actor_role"])
    op.create_index("ix_incident_history_created_at", "incident_history", ["created_at"])
    op.create_index("ix_incident_history_from_status", "incident_history", ["from_status"])
    op.create_index("ix_incident_history_incident_id", "incident_history", ["incident_id"])
    op.create_index("ix_incident_history_to_status", "incident_history", ["to_status"])

    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=True),
        sa.Column("message_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("requested_by", sa.String(length=120), nullable=False, server_default=sa.text("'manager'")),
        sa.Column("requested_by_role", sa.String(length=32), nullable=False, server_default=sa.text("'operator'")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'mock_sent'")),
        sa.Column("response_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notification_logs_channel", "notification_logs", ["channel"])
    op.create_index("ix_notification_logs_created_at", "notification_logs", ["created_at"])
    op.create_index("ix_notification_logs_incident_id", "notification_logs", ["incident_id"])
    op.create_index("ix_notification_logs_requested_by", "notification_logs", ["requested_by"])
    op.create_index("ix_notification_logs_requested_by_role", "notification_logs", ["requested_by_role"])
    op.create_index("ix_notification_logs_status", "notification_logs", ["status"])

    op.create_table(
        "remediation_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("approval_id", sa.Integer(), sa.ForeignKey("approvals.id"), nullable=False),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id"), nullable=True),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("command_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=48), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("output_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("approval_id", "phase", "step_order", name="uq_remediation_tasks_step"),
    )
    op.create_index("ix_remediation_tasks_approval_id", "remediation_tasks", ["approval_id"])
    op.create_index("ix_remediation_tasks_created_at", "remediation_tasks", ["created_at"])
    op.create_index("ix_remediation_tasks_incident_id", "remediation_tasks", ["incident_id"])
    op.create_index("ix_remediation_tasks_phase", "remediation_tasks", ["phase"])
    op.create_index("ix_remediation_tasks_status", "remediation_tasks", ["status"])

    op.create_table(
        "scan_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("requested_by", sa.String(length=120), nullable=False, server_default=sa.text("'system'")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_id", sa.Integer(), nullable=True),
        sa.Column("events_analyzed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("incidents_opened", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("incidents_resolved", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("incidents_touched", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index("ix_scan_history_requested_by", "scan_history", ["requested_by"])
    op.create_index("ix_scan_history_started_at", "scan_history", ["started_at"])
    op.create_index("ix_scan_history_status", "scan_history", ["status"])


def downgrade() -> None:
    op.drop_index("ix_scan_history_status", table_name="scan_history")
    op.drop_index("ix_scan_history_started_at", table_name="scan_history")
    op.drop_index("ix_scan_history_requested_by", table_name="scan_history")
    op.drop_table("scan_history")

    op.drop_index("ix_remediation_tasks_status", table_name="remediation_tasks")
    op.drop_index("ix_remediation_tasks_phase", table_name="remediation_tasks")
    op.drop_index("ix_remediation_tasks_incident_id", table_name="remediation_tasks")
    op.drop_index("ix_remediation_tasks_created_at", table_name="remediation_tasks")
    op.drop_index("ix_remediation_tasks_approval_id", table_name="remediation_tasks")
    op.drop_table("remediation_tasks")

    op.drop_index("ix_notification_logs_status", table_name="notification_logs")
    op.drop_index("ix_notification_logs_requested_by_role", table_name="notification_logs")
    op.drop_index("ix_notification_logs_requested_by", table_name="notification_logs")
    op.drop_index("ix_notification_logs_incident_id", table_name="notification_logs")
    op.drop_index("ix_notification_logs_created_at", table_name="notification_logs")
    op.drop_index("ix_notification_logs_channel", table_name="notification_logs")
    op.drop_table("notification_logs")

    op.drop_index("ix_incident_history_to_status", table_name="incident_history")
    op.drop_index("ix_incident_history_incident_id", table_name="incident_history")
    op.drop_index("ix_incident_history_from_status", table_name="incident_history")
    op.drop_index("ix_incident_history_created_at", table_name="incident_history")
    op.drop_index("ix_incident_history_actor_role", table_name="incident_history")
    op.drop_index("ix_incident_history_actor", table_name="incident_history")
    op.drop_index("ix_incident_history_action", table_name="incident_history")
    op.drop_table("incident_history")

    op.alter_column(
        "incidents",
        "status",
        existing_type=sa.String(length=32),
        server_default=sa.text("'open'"),
    )
    op.execute("UPDATE incidents SET status = 'open' WHERE status = 'new'")
    op.execute("UPDATE incidents SET status = 'investigating' WHERE status = 'in_progress'")
    op.drop_index("ix_incidents_assigned_to", table_name="incidents")
    op.drop_column("incidents", "resolution_notes")
    op.drop_column("incidents", "resolved_by")
    op.drop_column("incidents", "acknowledged_at")
    op.drop_column("incidents", "acknowledged_by")
    op.drop_column("incidents", "assigned_at")
    op.drop_column("incidents", "assigned_to")

    op.drop_index("ix_device_interfaces_protocol", table_name="device_interfaces")
    op.drop_index("ix_device_interfaces_name", table_name="device_interfaces")
    op.drop_index("ix_device_interfaces_last_state", table_name="device_interfaces")
    op.drop_index("ix_device_interfaces_last_event_time", table_name="device_interfaces")
    op.drop_index("ix_device_interfaces_last_event_id", table_name="device_interfaces")
    op.drop_index("ix_device_interfaces_device_id", table_name="device_interfaces")
    op.drop_table("device_interfaces")
