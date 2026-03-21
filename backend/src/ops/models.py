"""SQLAlchemy models for the operations platform."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.ops.db import Base, utcnow


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hostname: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    mgmt_ip: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    os_platform: Mapped[str] = mapped_column(String(80))
    device_role: Mapped[str] = mapped_column(String(80), index=True)
    site: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[str] = mapped_column(String(80), default="")
    vendor: Mapped[str] = mapped_column(String(80), default="")
    port: Mapped[int] = mapped_column(Integer, default=22)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(40), default="inventory_csv")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DeviceInterface(Base):
    __tablename__ = "device_interfaces"
    __table_args__ = (
        UniqueConstraint("device_id", "name", name="uq_device_interfaces_device_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    protocol: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_state: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    last_event_id: Mapped[int | None] = mapped_column(ForeignKey("normalized_events.id"), nullable=True, index=True)
    last_event_time: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RawLog(Base):
    __tablename__ = "raw_logs"
    __table_args__ = (
        UniqueConstraint("file_path", "offset_start", name="uq_raw_logs_path_offset"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_ip: Mapped[str] = mapped_column(String(64), index=True)
    file_path: Mapped[str] = mapped_column(String(512), index=True)
    offset_start: Mapped[int] = mapped_column(BigInteger)
    offset_end: Mapped[int] = mapped_column(BigInteger)
    log_time: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    raw_message: Mapped[str] = mapped_column(Text)
    ingested_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ingest_source: Mapped[str] = mapped_column(String(32), default="remote_file", index=True)
    collector_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    event_uid: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)


class SyslogCheckpoint(Base):
    __tablename__ = "syslog_checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_path: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    source_ip: Mapped[str] = mapped_column(String(64), index=True)
    offset: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NormalizedEvent(Base):
    __tablename__ = "normalized_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_log_id: Mapped[int] = mapped_column(ForeignKey("raw_logs.id"), unique=True, index=True)
    event_time: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    source_ip: Mapped[str] = mapped_column(String(64), index=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True, index=True)
    hostname: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    severity_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    facility: Mapped[str] = mapped_column(String(80), default="GENERIC", index=True)
    mnemonic: Mapped[str] = mapped_column(String(120), default="")
    event_code: Mapped[str] = mapped_column(String(160), default="", index=True)
    event_type: Mapped[str] = mapped_column(String(120), default="generic_syslog", index=True)
    protocol: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    interface_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    neighbor: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    correlation_key: Mapped[str] = mapped_column(String(240), default="", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_no: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    severity: Mapped[str] = mapped_column(String(32), default="warning", index=True)
    source: Mapped[str] = mapped_column(String(40), default="syslog", index=True)
    event_type: Mapped[str] = mapped_column(String(120), default="", index=True)
    correlation_key: Mapped[str] = mapped_column(String(240), default="", index=True)
    primary_device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True, index=True)
    primary_source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    probable_root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    affected_scope_json: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence_score: Mapped[int] = mapped_column(Integer, default=50)
    last_analysis_id: Mapped[int | None] = mapped_column(ForeignKey("llm_analyses.id"), nullable=True, index=True)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    assigned_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    acknowledged_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    requires_attention: Mapped[bool] = mapped_column(Boolean, default=True)
    last_event_time: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    opened_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    incident_cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("incident_clusters.id"), nullable=True, index=True
    )
    closed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IncidentHistory(Base):
    __tablename__ = "incident_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    actor: Mapped[str] = mapped_column(String(120), default="system", index=True)
    actor_role: Mapped[str] = mapped_column(String(32), default="system", index=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class IncidentEventLink(Base):
    __tablename__ = "incident_event_links"
    __table_args__ = (
        UniqueConstraint("incident_id", "event_id", name="uq_incident_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("normalized_events.id"), index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    target_ref: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    requested_by: Mapped[str] = mapped_column(String(120), default="manager")
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    requested_by: Mapped[str] = mapped_column(String(120), default="manager")
    requested_by_role: Mapped[str] = mapped_column(String(32), default="operator")
    reviewed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewed_by_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    executed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    executed_by_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_host: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    action_id: Mapped[str] = mapped_column(String(80), default="generic_config_change", index=True)
    commands_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollback_commands_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    verify_commands_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(32), default="medium", index=True)
    required_approval_role: Mapped[str] = mapped_column(String(32), default="approver")
    required_execution_role: Mapped[str] = mapped_column(String(32), default="operator")
    readiness: Mapped[str] = mapped_column(String(48), default="ready_for_human_review", index=True)
    readiness_score: Mapped[int] = mapped_column(Integer, default=50)
    execution_status: Mapped[str] = mapped_column(String(48), default="awaiting_approval", index=True)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    policy_snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    decided_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    channel: Mapped[str] = mapped_column(String(40), index=True)
    recipient: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_text: Mapped[str] = mapped_column(Text, default="")
    requested_by: Mapped[str] = mapped_column(String(120), default="manager", index=True)
    requested_by_role: Mapped[str] = mapped_column(String(32), default="operator", index=True)
    status: Mapped[str] = mapped_column(String(32), default="mock_sent", index=True)
    response_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class RemediationTask(Base):
    __tablename__ = "remediation_tasks"
    __table_args__ = (
        UniqueConstraint("approval_id", "phase", "step_order", name="uq_remediation_tasks_step"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    approval_id: Mapped[int] = mapped_column(ForeignKey("approvals.id"), index=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), nullable=True, index=True)
    phase: Mapped[str] = mapped_column(String(32), index=True)
    step_order: Mapped[int] = mapped_column(Integer)
    command_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(48), default="pending", index=True)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AuditEntry(Base):
    __tablename__ = "audit_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(120), default="system", index=True)
    actor_role: Mapped[str] = mapped_column(String(32), default="system", index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(48), default="recorded", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AIArtifact(Base):
    __tablename__ = "ai_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_type: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), nullable=True, index=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True, index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("approvals.id"), nullable=True, index=True)
    provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(80), default="v1")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[int] = mapped_column(Integer, default=50)
    readiness: Mapped[str] = mapped_column(String(48), default="informational")
    risk_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_refs_json: Mapped[dict] = mapped_column(JSON, default=dict)
    proposed_actions_json: Mapped[dict] = mapped_column(JSON, default=dict)
    content_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class LLMAnalysis(Base):
    __tablename__ = "llm_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), nullable=True, index=True)
    decision: Mapped[str] = mapped_column(String(32), default="no_issue", index=True)
    status: Mapped[str] = mapped_column(String(32), default="completed", index=True)
    window_start: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    window_end: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    input_log_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    open_incident_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(80), default="v1")
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ScanHistory(Base):
    __tablename__ = "scan_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requested_by: Mapped[str] = mapped_column(String(120), default="system", index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_created_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    events_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    incidents_opened: Mapped[int] = mapped_column(Integer, default=0)
    incidents_resolved: Mapped[int] = mapped_column(Integer, default=0)
    incidents_touched: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)


class IncidentFeedback(Base):
    __tablename__ = "incident_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    rating: Mapped[int] = mapped_column(Integer)
    was_false_positive: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution_effectiveness: Mapped[str] = mapped_column(String(32), default="unknown")
    operator_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(120), default="operator", index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class IncidentCluster(Base):
    __tablename__ = "incident_clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    root_cause_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(32), default="warning", index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
