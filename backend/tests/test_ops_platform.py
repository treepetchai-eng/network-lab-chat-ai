from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.ops.db import Base, _determine_bootstrap_action
from src.ops.incidents import correlate_event
from src.ops.models import (
    Approval,
    AuditEntry,
    Device,
    DeviceInterface,
    Incident,
    IncidentHistory,
    LLMAnalysis,
    Job,
    NormalizedEvent,
    NotificationLog,
    RawLog,
    RemediationTask,
    ScanHistory,
)
from src.ops.ai import analyze_device_focus_with_llm
from src.ops.service import (
    ConcurrentJobError,
    _SYNC_LOCKS,
    assign_incident,
    create_approval,
    execute_approval,
    get_device_detail,
    global_search_payload,
    ingest_syslog_push,
    list_events,
    notify_incident,
    review_approval,
    run_incident_scan,
    run_incident_investigation,
    run_inventory_sync,
    update_incident_status,
)
from src.ops.syslog_parser import parse_syslog_line


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    return TestingSession()


def test_parse_syslog_line_for_eigrp_down():
    event = parse_syslog_line(
        "Mar 16 11:17:55 10.255.3.101 %DUAL-5-NBRCHANGE: EIGRP-IPv4 100: Neighbor 172.16.20.1 (Tunnel20) is down: holding time expired",
        "/data/syslog/10.255.3.101/20260316.log",
    )
    assert event is not None
    assert event.event_type == "eigrp_neighbor_down"
    assert event.neighbor == "172.16.20.1"
    assert event.interface_name == "Tunnel20"
    assert event.correlation_key == "neighbor:EIGRP:10.255.3.101:172.16.20.1:Tunnel20"
    assert event.details["timestamp_source"] == "file_date"
    assert event.details["timestamp_year_adjustment"] == 0


def test_parse_syslog_line_rolls_back_future_rfc3164_timestamps():
    event = parse_syslog_line(
        "Aug 21 09:08:49 10.255.3.102 %TRACK-6-STATE: 30 ip sla 30 reachability Down -> Up",
        "/data/syslog/10.255.3.102/test.log",
        reference_time=datetime(2026, 3, 17, 16, 55, 42, tzinfo=timezone.utc),
    )
    assert event is not None
    assert event.event_time == datetime(2025, 8, 21, 9, 8, 49, tzinfo=timezone.utc)
    assert event.details["timestamp_source"] == "reference_time"
    assert event.details["timestamp_year_adjustment"] == -1


def test_correlate_event_opens_and_resolves_incident():
    session = make_session()
    device = Device(
        hostname="BRANCH-A-RTR",
        mgmt_ip="10.255.3.101",
        os_platform="cisco_ios",
        device_role="router",
        site="BRANCH-A",
        version="15.6(2)T",
        vendor="cisco",
    )
    session.add(device)
    session.flush()

    raw1 = RawLog(source_ip="10.255.3.101", file_path="/tmp/1.log", offset_start=0, offset_end=10, raw_message="down")
    session.add(raw1)
    session.flush()
    down_event = NormalizedEvent(
        raw_log_id=raw1.id,
        source_ip="10.255.3.101",
        device_id=device.id,
        hostname=device.hostname,
        severity="high",
        severity_num=5,
        facility="DUAL",
        mnemonic="NBRCHANGE",
        event_code="DUAL-5-NBRCHANGE",
        event_type="eigrp_neighbor_down",
        protocol="EIGRP",
        interface_name="Tunnel10",
        neighbor="172.16.10.1",
        state="DOWN",
        correlation_key="neighbor:EIGRP:10.255.3.101:172.16.10.1:Tunnel10",
        summary="EIGRP neighbor 172.16.10.1 went down",
        details_json={},
    )
    session.add(down_event)
    session.flush()
    opened, action = correlate_event(session, down_event)
    assert opened is not None
    assert action == "issue"
    assert opened.status == "new"

    raw2 = RawLog(source_ip="10.255.3.101", file_path="/tmp/1.log", offset_start=11, offset_end=20, raw_message="up")
    session.add(raw2)
    session.flush()
    up_event = NormalizedEvent(
        raw_log_id=raw2.id,
        source_ip="10.255.3.101",
        device_id=device.id,
        hostname=device.hostname,
        severity="informational",
        severity_num=5,
        facility="DUAL",
        mnemonic="NBRCHANGE",
        event_code="DUAL-5-NBRCHANGE",
        event_type="eigrp_neighbor_up",
        protocol="EIGRP",
        interface_name="Tunnel10",
        neighbor="172.16.10.1",
        state="UP",
        correlation_key="neighbor:EIGRP:10.255.3.101:172.16.10.1:Tunnel10",
        summary="EIGRP neighbor 172.16.10.1 recovered",
        details_json={},
    )
    session.add(up_event)
    session.flush()
    resolved, action = correlate_event(session, up_event)
    assert resolved is not None
    assert action in {"auto_resolved", "recovery_pending_verify", "flap_detected"}
    assert resolved.status == "resolved"
    assert resolved.event_count == 2


def test_execute_approval_read_only(monkeypatch):
    session = make_session()
    device = Device(
        hostname="BRANCH-B-RTR",
        mgmt_ip="10.255.3.102",
        os_platform="cisco_ios",
        device_role="router",
        site="BRANCH-B",
        version="15.6(2)T",
        vendor="cisco",
    )
    session.add(device)
    session.flush()

    approval_payload = create_approval(
        session,
        title="Read-only check",
        requested_by="manager",
        target_host=device.hostname,
        commands_text="show clock",
        rollback_commands_text="",
        verify_commands_text="show version | include uptime",
        rationale="Test",
        risk_level="low",
        notes="",
        incident_id=None,
    )
    approval = session.get(Approval, approval_payload["id"])
    assert approval is not None
    review_approval(session, approval.id, actor="manager", decision="approved")

    monkeypatch.setattr("src.ops.service.execute_cli", lambda ip, os_platform, command: f"{ip}:{command}:ok")
    monkeypatch.setattr("src.ops.service.execute_config", lambda ip, os_platform, commands: "should-not-run")

    result = execute_approval(session, approval.id, actor="manager")
    assert result["status"] == "executed"
    assert "show clock" in result["execution_output"]
    assert "should-not-run" not in result["execution_output"]


def test_device_analysis_retries_with_reasoning_when_fast_path_returns_empty(monkeypatch):
    session = make_session()
    device = Device(
        hostname="EDGE-RTR",
        mgmt_ip="10.1.1.1",
        os_platform="cisco_ios",
        device_role="router",
        site="EDGE",
        version="15.6(2)T",
        vendor="cisco",
    )
    session.add(device)
    session.flush()

    raw = RawLog(
        source_ip=device.mgmt_ip,
        file_path="/tmp/edge.log",
        offset_start=0,
        offset_end=10,
        raw_message="fault",
    )
    session.add(raw)
    session.flush()
    session.add(
        NormalizedEvent(
            raw_log_id=raw.id,
            source_ip=device.mgmt_ip,
            device_id=device.id,
            hostname=device.hostname,
            severity="critical",
            severity_num=5,
            facility="LINK",
            mnemonic="INTVULN",
            event_code="LINK-2-INTVULN",
            event_type="critical_region_fault",
            protocol=None,
            interface_name="GigabitEthernet0/0",
            neighbor=None,
            state="DOWN",
            correlation_key="fault:10.1.1.1:intvuln",
            summary="Critical-region fault reported on 10.1.1.1",
            details_json={},
        )
    )
    session.commit()

    calls: list[bool] = []

    class FakeModel:
        def __init__(self, content: str):
            self._content = content

        def invoke(self, prompt: str):
            return SimpleNamespace(content=self._content)

    def fake_create_chat_model(*, reasoning: bool = False):
        calls.append(reasoning)
        if len(calls) == 1:
            return FakeModel("")
        return FakeModel(
            json.dumps(
                {
                    "title": "Recovered via reasoning retry",
                    "summary": "Edge router fault summarized after retry.",
                    "impact": "Routing risk remains localized.",
                    "likely_cause": "Internal device fault.",
                    "next_checks": ["show logging"],
                    "proposed_actions": ["review traceback"],
                    "confidence_score": 70,
                    "risk_explanation": "Retry used reasoning model after empty fast response.",
                    "evidence_refs": ["event#1"],
                }
            )
        )

    monkeypatch.setattr("src.ops.ai.create_chat_model", fake_create_chat_model)
    monkeypatch.setattr(
        "src.ops.ai.resolve_llm_config",
        lambda reasoning=False: SimpleNamespace(provider="test", model="reasoning" if reasoning else "fast"),
    )

    result = analyze_device_focus_with_llm(session, device_id=device.id)

    assert calls == [False, True]
    assert result["title"] == "Recovered via reasoning retry"
    assert result["summary"] == "Edge router fault summarized after retry."


def test_execute_approval_config_mode(monkeypatch):
    session = make_session()
    device = Device(
        hostname="LAB-MGMT-BR01",
        mgmt_ip="10.255.0.1",
        os_platform="cisco_ios",
        device_role="router",
        site="MGMT",
        version="15.6(2)T",
        vendor="cisco",
    )
    session.add(device)
    session.flush()

    approval_payload = create_approval(
        session,
        title="Config path",
        requested_by="manager",
        target_host=device.hostname,
        commands_text="do show clock",
        rollback_commands_text="",
        verify_commands_text="show clock",
        rationale="Test config path",
        risk_level="low",
        notes="",
        incident_id=None,
    )
    approval = session.get(Approval, approval_payload["id"])
    assert approval is not None
    review_approval(session, approval.id, actor="manager", decision="approved")

    monkeypatch.setattr("src.ops.service.execute_config", lambda ip, os_platform, commands: f"{ip}:{'|'.join(commands)}:config")
    monkeypatch.setattr("src.ops.service.execute_cli", lambda ip, os_platform, command: f"{ip}:{command}:verify")

    result = execute_approval(session, approval.id, actor="manager")
    assert result["status"] == "executed"
    assert "config" in result["execution_output"]
    assert "verify" in result["execution_output"]


def test_execute_approval_marks_invalid_cli_output_as_failed_command(monkeypatch):
    session = make_session()
    device = Device(
        hostname="LAB-MGMT-BR02",
        mgmt_ip="10.255.0.22",
        os_platform="cisco_ios",
        device_role="router",
        site="MGMT",
        version="15.6(2)T",
        vendor="cisco",
    )
    session.add(device)
    session.flush()

    approval_payload = create_approval(
        session,
        title="Config path with syntax error",
        requested_by="manager",
        target_host=device.hostname,
        commands_text="interface Tunnel290\nip address 172.16.1.1 255.255.255.0",
        rollback_commands_text="no interface Tunnel290",
        verify_commands_text="show ip interface brief",
        rationale="Test embedded CLI error handling",
        risk_level="medium",
        notes="",
        incident_id=None,
    )
    approval = session.get(Approval, approval_payload["id"])
    assert approval is not None
    review_approval(session, approval.id, actor="manager", decision="approved")

    monkeypatch.setattr(
        "src.ops.service.execute_config",
        lambda ip, os_platform, commands: (
            "[CONFIG APPLIED] 10.255.0.22\n"
            "LAB(config)#interface Tunnel290\n"
            "LAB(config-if)#ip address 172.16.1.1 255.255.255.0\n"
            "            ^\n"
            "% Invalid input detected at '^' marker.\n"
        ),
    )

    with pytest.raises(Exception, match="CLI returned an error"):
        execute_approval(session, approval.id, actor="manager")

    approval = session.get(Approval, approval.id)
    assert approval is not None
    assert approval.status == "failed"
    assert approval.execution_status == "failed_command"


def test_execute_approval_raises_for_transport_errors(monkeypatch):
    session = make_session()
    device = Device(
        hostname="LAB-EDGE",
        mgmt_ip="10.255.0.2",
        os_platform="cisco_ios",
        device_role="router",
        site="EDGE",
        version="15.6(2)T",
        vendor="cisco",
    )
    session.add(device)
    session.flush()

    approval_payload = create_approval(
        session,
        title="Broken readonly",
        requested_by="manager",
        target_host=device.hostname,
        commands_text="show clock",
        rollback_commands_text="",
        verify_commands_text="",
        rationale="Test runtime failure",
        risk_level="low",
        notes="",
        incident_id=None,
    )
    approval = session.get(Approval, approval_payload["id"])
    assert approval is not None
    review_approval(session, approval.id, actor="manager", decision="approved")

    monkeypatch.setattr(
        "src.ops.service.execute_cli",
        lambda ip, os_platform, command: f"[TIMEOUT ERROR] {ip} timed out for {command}",
    )

    try:
        execute_approval(session, approval.id, actor="manager")
    except RuntimeError as exc:
        assert "Execution failed" in str(exc)
    else:
        raise AssertionError("execute_approval should raise on transport failures")

    assert approval.status == "failed"
    assert approval.execution_status == "failed_transport"
    assert approval.executed_at is None


def test_create_approval_records_policy_and_audit():
    session = make_session()
    payload = create_approval(
        session,
        title="Catalog-backed approval",
        requested_by="operator-a",
        requested_by_role="operator",
        target_host="BRANCH-A-RTR",
        action_id="check_bgp_neighbor",
        commands_text="show ip bgp summary",
        rollback_commands_text="",
        verify_commands_text="show ip bgp neighbors 10.0.0.1",
        rationale="Collect neighbor evidence",
        risk_level="low",
        notes="",
        incident_id=None,
        evidence_snapshot={"refs": ["event#1"]},
    )

    approval = session.get(Approval, payload["id"])
    assert approval is not None
    assert approval.action_id == "check_bgp_neighbor"
    assert approval.requested_by_role == "operator"
    assert approval.required_approval_role == "approver"
    assert approval.readiness in {"ready_for_human_review", "safe_for_low_risk_execution"}
    assert approval.evidence_snapshot_json["refs"] == ["event#1"]
    audit_count = session.query(AuditEntry).filter(AuditEntry.entity_type == "approval", AuditEntry.entity_id == approval.id).count()
    assert audit_count == 1


def test_critical_approval_requires_second_reviewer():
    session = make_session()
    device = Device(
        hostname="CORE-RTR",
        mgmt_ip="10.10.10.10",
        os_platform="cisco_ios",
        device_role="router",
        site="CORE",
        version="15.6(2)T",
        vendor="cisco",
    )
    session.add(device)
    session.flush()

    payload = create_approval(
        session,
        title="Critical rollback",
        requested_by="operator-a",
        requested_by_role="operator",
        target_host=device.hostname,
        action_id="restore_last_config",
        commands_text="configure replace flash:golden force",
        rollback_commands_text="show archive",
        verify_commands_text="show running-config | include hostname",
        rationale="Recover known-good config",
        risk_level="critical",
        notes="",
        incident_id=None,
    )
    approval = session.get(Approval, payload["id"])
    assert approval is not None

    first = review_approval(session, approval.id, actor="admin-a", actor_role="admin", decision="approved")
    assert first["status"] == "awaiting_second_approval"
    assert first["execution_status"] == "awaiting_second_approval"

    with pytest.raises(PermissionError):
        review_approval(session, approval.id, actor="admin-a", actor_role="admin", decision="approved")

    second = review_approval(session, approval.id, actor="admin-b", actor_role="admin", decision="approved")
    assert second["status"] == "approved"
    assert second["execution_status"] == "approved"


def test_device_detail_and_search_include_related_entities():
    session = make_session()
    device = Device(
        hostname="ACCESS-SW1",
        mgmt_ip="10.20.30.40",
        os_platform="cisco_ios",
        device_role="switch",
        site="BRANCH-X",
        version="15.6(2)T",
        vendor="cisco",
    )
    related = Device(
        hostname="ACCESS-SW2",
        mgmt_ip="10.20.30.41",
        os_platform="cisco_ios",
        device_role="switch",
        site="BRANCH-X",
        version="15.6(2)T",
        vendor="cisco",
    )
    session.add_all([device, related])
    session.flush()
    incident = Incident(
        title="Access instability",
        status="open",
        severity="warning",
        source="syslog",
        event_type="interface_down",
        correlation_key="iface:Gi1/0/1",
        primary_device_id=device.id,
        primary_source_ip=device.mgmt_ip,
        summary="Port flaps observed",
        event_count=1,
    )
    session.add(incident)
    session.commit()

    detail = get_device_detail(session, device.id)
    assert detail is not None
    assert detail["hostname"] == "ACCESS-SW1"
    assert detail["blast_radius"]["site"] == "BRANCH-X"
    assert len(detail["related_devices"]) == 1

    search = global_search_payload(session, "ACCESS")
    assert search["devices"][0]["title"] == "ACCESS-SW1"


def test_run_inventory_sync_persists_failed_job(monkeypatch):
    session = make_session()
    monkeypatch.setattr("src.ops.service.sync_inventory_from_csv", lambda current_session: (_ for _ in ()).throw(RuntimeError("csv exploded")))

    try:
        run_inventory_sync(session, requested_by="tester")
    except RuntimeError as exc:
        assert str(exc) == "csv exploded"
    else:
        raise AssertionError("run_inventory_sync should raise when inventory sync fails")

    assert session.query(RawLog).count() == 0
    job = session.query(Job).order_by(Job.id.desc()).first()
    assert job is not None
    assert job.status == "failed"
    assert job.error_text == "csv exploded"


def test_run_incident_investigation_persists_failure(monkeypatch):
    session = make_session()
    device = Device(
        hostname="BRANCH-C-RTR",
        mgmt_ip="10.255.3.103",
        os_platform="cisco_ios",
        device_role="router",
        site="BRANCH-C",
        version="15.6(2)T",
        vendor="cisco",
    )
    session.add(device)
    session.flush()

    incident = Incident(
        title="Broken incident",
        status="open",
        severity="high",
        source="syslog",
        event_type="bgp_neighbor_down",
        correlation_key="neighbor:BGP:10.255.3.103:10.0.0.1:Gi0/0",
        primary_device_id=device.id,
        primary_source_ip=device.mgmt_ip,
        summary="BGP neighbor is down",
        event_count=1,
    )
    session.add(incident)
    session.commit()

    monkeypatch.setattr(
        "src.ops.service.investigate_incident_with_llm",
        lambda current_session, incident_id: (_ for _ in ()).throw(RuntimeError("llm offline")),
    )

    try:
        run_incident_investigation(session, incident.id, requested_by="tester")
    except RuntimeError as exc:
        assert str(exc) == "llm offline"
    else:
        raise AssertionError("run_incident_investigation should raise when investigation fails")

    session.refresh(incident)
    assert incident.status == "new"
    job = session.query(Job).order_by(Job.id.desc()).first()
    assert job is not None
    assert job.status == "failed"
    assert job.error_text == "llm offline"


def test_run_inventory_sync_rejects_overlapping_execution():
    session = make_session()
    lock = _SYNC_LOCKS["inventory"]
    acquired = lock.acquire(blocking=False)
    assert acquired
    try:
        with pytest.raises(ConcurrentJobError):
            run_inventory_sync(session, requested_by="tester")
    finally:
        lock.release()


def test_determine_bootstrap_action_stamps_existing_schema():
    action = _determine_bootstrap_action(
        existing_tables={
            "devices",
            "raw_logs",
            "syslog_checkpoints",
            "normalized_events",
            "incidents",
            "incident_event_links",
            "jobs",
            "approvals",
        },
        managed_tables={"devices", "raw_logs", "jobs"},
    )
    assert action == "stamp_existing"


def test_determine_bootstrap_action_upgrades_when_version_table_exists():
    action = _determine_bootstrap_action(
        existing_tables={"alembic_version", "devices"},
        managed_tables={"devices", "raw_logs", "jobs"},
    )
    assert action == "upgrade"


def test_determine_bootstrap_action_rejects_partial_existing_schema():
    action = _determine_bootstrap_action(
        existing_tables={"devices"},
        managed_tables={"devices", "raw_logs", "jobs"},
    )
    assert action == "error_partial_schema"


def test_list_events_supports_date_filters_and_facets():
    session = make_session()
    device = Device(
        hostname="BRANCH-D-RTR",
        mgmt_ip="10.255.3.104",
        os_platform="cisco_ios",
        device_role="router",
        site="BRANCH-D",
        version="15.6(2)T",
        vendor="cisco",
    )
    session.add(device)
    session.flush()

    now = datetime(2026, 3, 18, 9, 0, tzinfo=timezone.utc)

    raw1 = RawLog(source_ip=device.mgmt_ip, file_path="/tmp/1.log", offset_start=0, offset_end=10, raw_message="one")
    raw2 = RawLog(source_ip=device.mgmt_ip, file_path="/tmp/1.log", offset_start=11, offset_end=20, raw_message="two")
    session.add_all([raw1, raw2])
    session.flush()

    session.add_all([
        NormalizedEvent(
            raw_log_id=raw1.id,
            event_time=now - timedelta(days=2),
            source_ip=device.mgmt_ip,
            device_id=device.id,
            hostname=device.hostname,
            severity="warning",
            severity_num=4,
            facility="LINEPROTO",
            event_code="LINEPROTO-5-UPDOWN",
            event_type="interface_down",
            interface_name="Gi0/0",
            correlation_key="iface:Gi0/0",
            summary="Interface down",
            details_json={},
        ),
        NormalizedEvent(
            raw_log_id=raw2.id,
            event_time=now,
            source_ip=device.mgmt_ip,
            device_id=device.id,
            hostname=device.hostname,
            severity="critical",
            severity_num=2,
            facility="BGP",
            event_code="BGP-3-NOTIFICATION",
            event_type="bgp_neighbor_down",
            neighbor="10.0.0.1",
            correlation_key="neighbor:10.0.0.1",
            summary="BGP neighbor down",
            details_json={},
        ),
    ])
    session.commit()

    payload = list_events(
        session,
        event_from="2026-03-18",
        event_to="2026-03-18",
        page_size=25,
    )

    assert payload["total"] == 1
    assert payload["items"][0]["event_type"] == "bgp_neighbor_down"
    assert payload["facets"]["severities"] == ["critical", "warning"]
    assert payload["facets"]["event_types"] == ["bgp_neighbor_down", "interface_down"]


def test_ingest_syslog_push_creates_raw_event_without_immediate_incident():
    session = make_session()
    device = Device(
        hostname="BRANCH-PUSH-RTR",
        mgmt_ip="10.255.9.1",
        os_platform="cisco_ios",
        device_role="router",
        site="BRANCH-PUSH",
        version="15.6(2)T",
        vendor="cisco",
    )
    session.add(device)
    session.commit()

    result = ingest_syslog_push(
        session,
        collector_name="syslog-ng",
        events=[
            {
                "source_ip": device.mgmt_ip,
                "raw_message": "Mar 18 13:30:00 10.255.9.1 %BGP-5-ADJCHANGE: neighbor 172.16.90.1 Down BGP Notification sent",
                "file_path": "/ingest/http/syslog-ng/10.255.9.1/20260318.log",
                "collector_time": datetime(2026, 3, 18, 13, 30, tzinfo=timezone.utc),
                "metadata": {"transport": "http"},
            }
        ],
    )

    assert result["received"] == 1
    assert result["duplicates"] == 0
    assert result["raw_logs"] == 1
    assert result["events"] == 1
    assert result["incidents_touched"] == 0

    raw_log = session.query(RawLog).one()
    event = session.query(NormalizedEvent).one()

    assert raw_log.ingest_source == "http_push"
    assert raw_log.collector_name == "syslog-ng"
    assert raw_log.event_uid
    assert event.source_ip == device.mgmt_ip
    assert event.device_id == device.id
    assert event.details_json["ingest_source"] == "http_push"
    assert event.details_json["collector_metadata"]["transport"] == "http"
    assert session.query(Incident).count() == 0


def test_ingest_syslog_push_updates_device_interface_inventory():
    session = make_session()
    device = Device(
        hostname="BRANCH-IFACE-RTR",
        mgmt_ip="10.255.9.2",
        os_platform="cisco_ios",
        device_role="router",
        site="BRANCH-IFACE",
        version="15.6(2)T",
        vendor="cisco",
    )
    session.add(device)
    session.commit()

    ingest_syslog_push(
        session,
        collector_name="syslog-ng",
        events=[
            {
                "source_ip": device.mgmt_ip,
                "raw_message": "Mar 18 13:35:00 10.255.9.2 %LINEPROTO-5-UPDOWN: Line protocol on Interface Tunnel77, changed state to down",
                "file_path": "/ingest/http/syslog-ng/10.255.9.2/20260318.log",
                "collector_time": datetime(2026, 3, 18, 13, 35, tzinfo=timezone.utc),
                "metadata": {"transport": "http"},
            }
        ],
    )

    interface = session.query(DeviceInterface).one()
    assert interface.device_id == device.id
    assert interface.name == "Tunnel77"
    assert interface.last_state == "DOWN"
    assert interface.event_count == 1


def test_incident_workflow_status_assignment_and_notification():
    session = make_session()
    device = Device(
        hostname="BRANCH-WF-RTR",
        mgmt_ip="10.255.3.111",
        os_platform="cisco_ios",
        device_role="router",
        site="BRANCH-WF",
        version="15.6(2)T",
        vendor="cisco",
    )
    session.add(device)
    session.flush()
    incident = Incident(
        title="Workflow incident",
        status="new",
        severity="high",
        source="syslog",
        event_type="interface_down",
        correlation_key="iface:10.255.3.111:Tunnel55",
        primary_device_id=device.id,
        primary_source_ip=device.mgmt_ip,
        summary="Tunnel55 is down",
        event_count=1,
    )
    session.add(incident)
    session.commit()

    update_incident_status(session, incident.id, status="acknowledged", actor="lead", actor_role="approver", comment="Taking ownership")
    assign_incident(session, incident.id, assignee="netops-a", actor="lead", actor_role="approver", comment="Primary handler")
    notify_incident(session, incident.id, channel="slack", actor="lead", actor_role="approver", recipient="#noc")
    update_incident_status(session, incident.id, status="resolved", actor="lead", actor_role="approver", comment="Recovered after provider fix")
    session.commit()

    session.refresh(incident)
    assert incident.status == "resolved"
    assert incident.assigned_to == "netops-a"
    assert incident.resolution_notes == "Recovered after provider fix"
    assert session.query(NotificationLog).count() == 1
    assert session.query(IncidentHistory).count() >= 4


def test_remediation_tasks_created_and_scan_history_persisted(monkeypatch):
    session = make_session()
    device = Device(
        hostname="BRANCH-SCAN-RTR",
        mgmt_ip="10.255.3.112",
        os_platform="cisco_ios",
        device_role="router",
        site="BRANCH-SCAN",
        version="15.6(2)T",
        vendor="cisco",
    )
    session.add(device)
    session.flush()

    approval_payload = create_approval(
        session,
        title="Readonly diag",
        requested_by="manager",
        target_host=device.hostname,
        commands_text="show clock\nshow version",
        rollback_commands_text="",
        verify_commands_text="show ip interface brief",
        rationale="Collect evidence",
        risk_level="low",
        notes="",
        incident_id=None,
    )
    approval = session.get(Approval, approval_payload["id"])
    assert approval is not None
    assert session.query(RemediationTask).filter(RemediationTask.approval_id == approval.id).count() == 3

    raw_log = RawLog(
        source_ip=device.mgmt_ip,
        file_path="/tmp/scan.log",
        offset_start=0,
        offset_end=10,
        raw_message="lineproto down",
    )
    session.add(raw_log)
    session.flush()
    event = NormalizedEvent(
        raw_log_id=raw_log.id,
        event_time=datetime.now(timezone.utc),
        source_ip=device.mgmt_ip,
        device_id=device.id,
        hostname=device.hostname,
        severity="medium",
        facility="LINEPROTO",
        mnemonic="UPDOWN",
        event_code="LINEPROTO-5-UPDOWN",
        event_type="interface_down",
        interface_name="Tunnel55",
        state="DOWN",
        correlation_key="iface:10.255.3.112:Tunnel55",
        summary="Tunnel55 down",
        details_json={},
    )
    session.add(event)
    session.commit()

    def fake_analyze_log_window(current_session, *, raw_logs, open_incidents, window_start, window_end):
        analysis = LLMAnalysis(
            decision="create_incident",
            status="completed",
            window_start=window_start,
            window_end=window_end,
            input_log_ids_json=[item.id for item in raw_logs],
            open_incident_ids_json=[item.id for item in open_incidents],
            provider="test",
            model="test-model",
            prompt_version="test",
            raw_text='{"decision":"create_incident"}',
            output_json={
                "decision": "create_incident",
                "incident_title": "Tunnel55 down on BRANCH-SCAN-RTR",
                "severity": "medium",
                "summary": "Tunnel55 went down and needs investigation.",
                "probable_root_cause": "Interface Tunnel55 is down on BRANCH-SCAN-RTR.",
                "affected_scope": ["BRANCH-SCAN-RTR"],
                "confidence": 0.86,
                "evidence_log_ids": [item.id for item in raw_logs],
            },
        )
        current_session.add(analysis)
        current_session.flush()
        return {
            "analysis_id": analysis.id,
            "decision": "create_incident",
            "parsed": dict(analysis.output_json or {}),
            "raw_text": analysis.raw_text or "",
            "provider": analysis.provider,
            "model": analysis.model,
            "events_by_log_id": {event.raw_log_id: event},
        }

    monkeypatch.setattr("src.ops.service.analyze_log_window_with_llm", fake_analyze_log_window)

    result = run_incident_scan(session, requested_by="scanner")
    assert result["logs_analyzed"] >= 1
    assert result["incidents_created"] == 1
    assert session.query(ScanHistory).count() == 1
    incident = session.query(Incident).one()
    assert incident.ai_summary
    assert incident.probable_root_cause == "Interface Tunnel55 is down on BRANCH-SCAN-RTR."
    assert incident.confidence_score == 86
    assert incident.last_analysis_id is not None


def test_ingest_syslog_push_deduplicates_on_stable_payload():
    session = make_session()
    collector_time = datetime(2026, 3, 18, 13, 45, tzinfo=timezone.utc)
    payload = {
        "source_ip": "10.255.9.2",
        "raw_message": "Mar 18 13:45:00 10.255.9.2 %TRACK-6-STATE: 10 ip sla 10 reachability Down -> Up",
        "file_path": "/ingest/http/syslog-ng/10.255.9.2/20260318.log",
        "collector_time": collector_time,
    }

    first = ingest_syslog_push(session, collector_name="syslog-ng", events=[payload])
    second = ingest_syslog_push(session, collector_name="syslog-ng", events=[payload])

    assert first["raw_logs"] == 1
    assert second["duplicates"] == 1
    assert session.query(RawLog).count() == 1
    assert session.query(NormalizedEvent).count() == 1


def test_ingest_syslog_push_reconstructs_event_from_rfc5424_envelope():
    session = make_session()
    payload = {
        "source_ip": "127.0.0.1",
        "raw_message": '1 2026-03-18T13:41:23.008734+07:00 syslog treepetch - - [timeQuality tzKnown="1"] %LINEPROTO-5-UPDOWN: Line protocol on Interface Tunnel92, changed state to down',
        "file_path": "/data/syslog/127.0.0.1/20260318.log",
        "collector_time": datetime(2026, 3, 18, 13, 41, 23, tzinfo=timezone.utc),
    }

    result = ingest_syslog_push(session, collector_name="syslog-ng", events=[payload])

    assert result["events"] == 1
    event = session.query(NormalizedEvent).one()
    assert event.event_type == "interface_down"
    assert event.interface_name == "Tunnel92"
    assert event.source_ip == "127.0.0.1"
    assert event.details_json["collector_reconstructed_header"] is True


def test_troubleshoot_creates_approval_from_structured_config_proposal(monkeypatch):
    from src.ops import free_run

    session = make_session()
    device = Device(
        hostname="BRANCH-PROPOSAL-RTR",
        mgmt_ip="10.255.9.9",
        os_platform="cisco_ios",
        device_role="router",
        site="BRANCH-PROPOSAL",
        version="15.6(2)T",
        vendor="cisco",
    )
    session.add(device)
    session.flush()
    incident = Incident(
        title="Tunnel290 missing after config change",
        status="new",
        severity="medium",
        source="llm_analyzer",
        event_type="interface_down",
        correlation_key="llm:10.255.9.9:interface_down",
        primary_device_id=device.id,
        primary_source_ip=device.mgmt_ip,
        summary="Tunnel290 disappeared after a console configuration change.",
        ai_summary="Tunnel290 disappeared after a console configuration change.",
        probable_root_cause="Interface stanza was removed from running-config.",
        event_count=1,
    )
    session.add(incident)
    session.commit()

    @contextmanager
    def fake_scope():
        yield session

    async def fake_create_session():
        return SimpleNamespace(session_id="graph-1", device_cache={}, progress_sink={})

    async def fake_delete_session(_session_id: str):
        return None

    async def fake_stream_chat(_graph_session, _prompt):
        yield {"event": "status", "data": {"text": "Collecting evidence..."}}
        yield {"event": "tool_result", "data": {"step_name": "show running-config", "content": "interface missing", "is_error": False}}
        yield {"event": "analyst_done", "data": {"full_content": "Tunnel290 was removed from configuration."}}

    monkeypatch.setattr(free_run, "session_scope", fake_scope)
    monkeypatch.setattr(free_run, "create_session", fake_create_session)
    monkeypatch.setattr(free_run, "delete_session", fake_delete_session)
    monkeypatch.setattr(free_run, "stream_chat", fake_stream_chat)
    monkeypatch.setattr(
        free_run,
        "synthesize_troubleshoot_result_with_llm",
        lambda **_kwargs: {
            "summary": "Tunnel290 was deleted from config.",
            "diagnosis_type": "config",
            "probable_root_cause": "Manual removal of interface Tunnel290 from running-config.",
            "evidence_refs": ["step#1"],
            "recommended_next_action": "Restore the interface definition.",
            "proposal": {
                "target_host": device.hostname,
                "commands": [
                    "interface Tunnel290",
                    "tunnel source Loopback0",
                ],
                "verify_commands": ["show ip interface brief"],
                "rollback_commands": ["no interface Tunnel290"],
                "risk_level": "medium",
                "rationale": "Restore the missing tunnel definition.",
            },
            "provider": "test",
            "model": "test-model",
            "prompt_version": "test",
        },
    )

    result = asyncio.run(
        free_run.run_incident_troubleshoot_free_run(
            incident.id,
            requested_by="manager",
            requested_by_role="admin",
        )
    )

    assert result["diagnosis_type"] == "config"
    assert result["approval_id"] is not None
    approval = session.get(Approval, result["approval_id"])
    assert approval is not None
    assert approval.incident_id == incident.id
    assert approval.target_host == device.hostname
