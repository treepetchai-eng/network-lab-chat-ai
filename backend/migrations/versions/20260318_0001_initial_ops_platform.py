"""Initial operations platform schema.

Revision ID: 20260318_0001
Revises:
Create Date: 2026-03-18 00:01:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260318_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("hostname", sa.String(length=120), nullable=False),
        sa.Column("mgmt_ip", sa.String(length=64), nullable=False),
        sa.Column("os_platform", sa.String(length=80), nullable=False),
        sa.Column("device_role", sa.String(length=80), nullable=False),
        sa.Column("site", sa.String(length=80), nullable=False),
        sa.Column("version", sa.String(length=80), nullable=False, server_default=sa.text("''")),
        sa.Column("vendor", sa.String(length=80), nullable=False, server_default=sa.text("''")),
        sa.Column("port", sa.Integer(), nullable=False, server_default=sa.text("22")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("source", sa.String(length=40), nullable=False, server_default=sa.text("'inventory_csv'")),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_devices_device_role", "devices", ["device_role"])
    op.create_index("ix_devices_hostname", "devices", ["hostname"], unique=True)
    op.create_index("ix_devices_mgmt_ip", "devices", ["mgmt_ip"], unique=True)
    op.create_index("ix_devices_site", "devices", ["site"])

    op.create_table(
        "raw_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_ip", sa.String(length=64), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("offset_start", sa.BigInteger(), nullable=False),
        sa.Column("offset_end", sa.BigInteger(), nullable=False),
        sa.Column("log_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_message", sa.Text(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("file_path", "offset_start", name="uq_raw_logs_path_offset"),
    )
    op.create_index("ix_raw_logs_file_path", "raw_logs", ["file_path"])
    op.create_index("ix_raw_logs_ingested_at", "raw_logs", ["ingested_at"])
    op.create_index("ix_raw_logs_log_time", "raw_logs", ["log_time"])
    op.create_index("ix_raw_logs_source_ip", "raw_logs", ["source_ip"])

    op.create_table(
        "syslog_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("source_ip", sa.String(length=64), nullable=False),
        sa.Column("offset", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_syslog_checkpoints_file_path", "syslog_checkpoints", ["file_path"], unique=True)
    op.create_index("ix_syslog_checkpoints_source_ip", "syslog_checkpoints", ["source_ip"])

    op.create_table(
        "normalized_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("raw_log_id", sa.Integer(), sa.ForeignKey("raw_logs.id"), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_ip", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id"), nullable=True),
        sa.Column("hostname", sa.String(length=120), nullable=True),
        sa.Column("severity_num", sa.Integer(), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("facility", sa.String(length=80), nullable=False, server_default=sa.text("'GENERIC'")),
        sa.Column("mnemonic", sa.String(length=120), nullable=False, server_default=sa.text("''")),
        sa.Column("event_code", sa.String(length=160), nullable=False, server_default=sa.text("''")),
        sa.Column("event_type", sa.String(length=120), nullable=False, server_default=sa.text("'generic_syslog'")),
        sa.Column("protocol", sa.String(length=80), nullable=True),
        sa.Column("interface_name", sa.String(length=120), nullable=True),
        sa.Column("neighbor", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=80), nullable=True),
        sa.Column("correlation_key", sa.String(length=240), nullable=False, server_default=sa.text("''")),
        sa.Column("summary", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_normalized_events_correlation_key", "normalized_events", ["correlation_key"])
    op.create_index("ix_normalized_events_created_at", "normalized_events", ["created_at"])
    op.create_index("ix_normalized_events_device_id", "normalized_events", ["device_id"])
    op.create_index("ix_normalized_events_event_code", "normalized_events", ["event_code"])
    op.create_index("ix_normalized_events_event_time", "normalized_events", ["event_time"])
    op.create_index("ix_normalized_events_event_type", "normalized_events", ["event_type"])
    op.create_index("ix_normalized_events_facility", "normalized_events", ["facility"])
    op.create_index("ix_normalized_events_hostname", "normalized_events", ["hostname"])
    op.create_index("ix_normalized_events_interface_name", "normalized_events", ["interface_name"])
    op.create_index("ix_normalized_events_neighbor", "normalized_events", ["neighbor"])
    op.create_index("ix_normalized_events_protocol", "normalized_events", ["protocol"])
    op.create_index("ix_normalized_events_raw_log_id", "normalized_events", ["raw_log_id"], unique=True)
    op.create_index("ix_normalized_events_severity", "normalized_events", ["severity"])
    op.create_index("ix_normalized_events_source_ip", "normalized_events", ["source_ip"])

    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'open'")),
        sa.Column("severity", sa.String(length=32), nullable=False, server_default=sa.text("'warning'")),
        sa.Column("source", sa.String(length=40), nullable=False, server_default=sa.text("'syslog'")),
        sa.Column("event_type", sa.String(length=120), nullable=False, server_default=sa.text("''")),
        sa.Column("correlation_key", sa.String(length=240), nullable=False, server_default=sa.text("''")),
        sa.Column("primary_device_id", sa.Integer(), sa.ForeignKey("devices.id"), nullable=True),
        sa.Column("primary_source_ip", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("requires_attention", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_incidents_correlation_key", "incidents", ["correlation_key"])
    op.create_index("ix_incidents_event_type", "incidents", ["event_type"])
    op.create_index("ix_incidents_last_event_time", "incidents", ["last_event_time"])
    op.create_index("ix_incidents_opened_at", "incidents", ["opened_at"])
    op.create_index("ix_incidents_primary_device_id", "incidents", ["primary_device_id"])
    op.create_index("ix_incidents_primary_source_ip", "incidents", ["primary_source_ip"])
    op.create_index("ix_incidents_severity", "incidents", ["severity"])
    op.create_index("ix_incidents_source", "incidents", ["source"])
    op.create_index("ix_incidents_status", "incidents", ["status"])

    op.create_table(
        "incident_event_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("normalized_events.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("incident_id", "event_id", name="uq_incident_event"),
    )
    op.create_index("ix_incident_event_links_event_id", "incident_event_links", ["event_id"])
    op.create_index("ix_incident_event_links_incident_id", "incident_event_links", ["incident_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("target_type", sa.String(length=80), nullable=True),
        sa.Column("target_ref", sa.String(length=120), nullable=True),
        sa.Column("requested_by", sa.String(length=120), nullable=False, server_default=sa.text("'manager'")),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])
    op.create_index("ix_jobs_job_type", "jobs", ["job_type"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_target_ref", "jobs", ["target_ref"])
    op.create_index("ix_jobs_target_type", "jobs", ["target_type"])

    op.create_table(
        "approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id"), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("requested_by", sa.String(length=120), nullable=False, server_default=sa.text("'manager'")),
        sa.Column("reviewed_by", sa.String(length=120), nullable=True),
        sa.Column("target_host", sa.String(length=120), nullable=True),
        sa.Column("commands_text", sa.Text(), nullable=True),
        sa.Column("rollback_commands_text", sa.Text(), nullable=True),
        sa.Column("verify_commands_text", sa.Text(), nullable=True),
        sa.Column("diff_text", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(length=32), nullable=False, server_default=sa.text("'medium'")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("execution_output", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_approvals_incident_id", "approvals", ["incident_id"])
    op.create_index("ix_approvals_job_id", "approvals", ["job_id"])
    op.create_index("ix_approvals_requested_at", "approvals", ["requested_at"])
    op.create_index("ix_approvals_risk_level", "approvals", ["risk_level"])
    op.create_index("ix_approvals_status", "approvals", ["status"])
    op.create_index("ix_approvals_target_host", "approvals", ["target_host"])


def downgrade() -> None:
    op.drop_index("ix_approvals_target_host", table_name="approvals")
    op.drop_index("ix_approvals_status", table_name="approvals")
    op.drop_index("ix_approvals_risk_level", table_name="approvals")
    op.drop_index("ix_approvals_requested_at", table_name="approvals")
    op.drop_index("ix_approvals_job_id", table_name="approvals")
    op.drop_index("ix_approvals_incident_id", table_name="approvals")
    op.drop_table("approvals")

    op.drop_index("ix_jobs_target_type", table_name="jobs")
    op.drop_index("ix_jobs_target_ref", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_job_type", table_name="jobs")
    op.drop_index("ix_jobs_created_at", table_name="jobs")
    op.drop_table("jobs")

    op.drop_index("ix_incident_event_links_incident_id", table_name="incident_event_links")
    op.drop_index("ix_incident_event_links_event_id", table_name="incident_event_links")
    op.drop_table("incident_event_links")

    op.drop_index("ix_incidents_status", table_name="incidents")
    op.drop_index("ix_incidents_source", table_name="incidents")
    op.drop_index("ix_incidents_severity", table_name="incidents")
    op.drop_index("ix_incidents_primary_source_ip", table_name="incidents")
    op.drop_index("ix_incidents_primary_device_id", table_name="incidents")
    op.drop_index("ix_incidents_opened_at", table_name="incidents")
    op.drop_index("ix_incidents_last_event_time", table_name="incidents")
    op.drop_index("ix_incidents_event_type", table_name="incidents")
    op.drop_index("ix_incidents_correlation_key", table_name="incidents")
    op.drop_table("incidents")

    op.drop_index("ix_normalized_events_source_ip", table_name="normalized_events")
    op.drop_index("ix_normalized_events_severity", table_name="normalized_events")
    op.drop_index("ix_normalized_events_raw_log_id", table_name="normalized_events")
    op.drop_index("ix_normalized_events_protocol", table_name="normalized_events")
    op.drop_index("ix_normalized_events_neighbor", table_name="normalized_events")
    op.drop_index("ix_normalized_events_interface_name", table_name="normalized_events")
    op.drop_index("ix_normalized_events_hostname", table_name="normalized_events")
    op.drop_index("ix_normalized_events_facility", table_name="normalized_events")
    op.drop_index("ix_normalized_events_event_type", table_name="normalized_events")
    op.drop_index("ix_normalized_events_event_time", table_name="normalized_events")
    op.drop_index("ix_normalized_events_event_code", table_name="normalized_events")
    op.drop_index("ix_normalized_events_device_id", table_name="normalized_events")
    op.drop_index("ix_normalized_events_created_at", table_name="normalized_events")
    op.drop_index("ix_normalized_events_correlation_key", table_name="normalized_events")
    op.drop_table("normalized_events")

    op.drop_index("ix_syslog_checkpoints_source_ip", table_name="syslog_checkpoints")
    op.drop_index("ix_syslog_checkpoints_file_path", table_name="syslog_checkpoints")
    op.drop_table("syslog_checkpoints")

    op.drop_index("ix_raw_logs_source_ip", table_name="raw_logs")
    op.drop_index("ix_raw_logs_log_time", table_name="raw_logs")
    op.drop_index("ix_raw_logs_ingested_at", table_name="raw_logs")
    op.drop_index("ix_raw_logs_file_path", table_name="raw_logs")
    op.drop_table("raw_logs")

    op.drop_index("ix_devices_site", table_name="devices")
    op.drop_index("ix_devices_mgmt_ip", table_name="devices")
    op.drop_index("ix_devices_hostname", table_name="devices")
    op.drop_index("ix_devices_device_role", table_name="devices")
    op.drop_table("devices")
