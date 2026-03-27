from __future__ import annotations

import time
from unittest.mock import patch

from fastapi.testclient import TestClient
from psycopg.types.json import Json

from src.aiops.db import connect
from src.api import _aiops_service, app

PAYLOAD = {
    "source_ip": "10.255.1.11",
    "hostname": "HQ-CORE-RT01",
    "raw_message": "%BGP-5-ADJCHANGE: neighbor 10.255.254.2 Down BGP Notification",
}

ADMIN_DOWN_PAYLOAD = {
    "source_ip": "10.255.1.11",
    "hostname": "HQ-CORE-RT01",
    "raw_message": "%LINK-5-CHANGED: Interface GigabitEthernet0/0, changed state to administratively down",
    "event_time": "2026-03-26T10:00:00Z",
}

REMOTE_BGP_DOWN_PAYLOAD = {
    "source_ip": "10.255.1.12",
    "hostname": "HQ-CORE-RT02",
    "raw_message": "%BGP-5-ADJCHANGE: neighbor 10.255.10.9 Down BGP Notification",
    "event_time": "2026-03-26T10:00:05Z",
}

RECOVERY_PAYLOAD = {
    "source_ip": "10.255.1.11",
    "hostname": "HQ-CORE-RT01",
    "raw_message": "%BGP-5-ADJCHANGE: neighbor 10.255.254.2 Up",
}


def _create_incident(client: TestClient) -> str:
    reset_before = client.post("/api/aiops/incidents/reset")
    assert reset_before.status_code == 200

    ingest = client.post("/api/aiops/logs/ingest", json=PAYLOAD)
    assert ingest.status_code == 200

    for _ in range(30):
        incidents_response = client.get("/api/aiops/incidents")
        assert incidents_response.status_code == 200
        incidents = incidents_response.json()
        if incidents:
            return incidents[0]["incident_no"]
        time.sleep(0.1)
    raise AssertionError("incident was not created")


def _create_linked_incidents(client: TestClient) -> tuple[dict, dict]:
    client.post("/api/aiops/incidents/reset")
    client.post("/api/aiops/logs/ingest", json=ADMIN_DOWN_PAYLOAD)
    client.post("/api/aiops/logs/ingest", json=REMOTE_BGP_DOWN_PAYLOAD)
    incidents: list[dict] = []
    for _ in range(20):
        incidents = client.get("/api/aiops/incidents").json()
        if len(incidents) == 2:
            break
        time.sleep(0.1)
    assert len(incidents) == 2
    owner = next(incident for incident in incidents if incident["workflow_phase"] == "intent_confirmation_required")
    peer = next(incident for incident in incidents if incident["incident_no"] != owner["incident_no"])
    return owner, peer


def _seed_status(
    incident_no: str,
    *,
    status: str,
    current_recovery_state: str,
    workflow_phase: str = "none",
    age_seconds: int = 0,
    resolution_type: str | None = None,
) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE incidents
                SET status = %s,
                    workflow_phase = %s,
                    current_recovery_state = %s,
                    last_seen_at = NOW() - (interval '1 second' * %s),
                    resolution_type = %s,
                    resolved_at = NULL,
                    updated_at = NOW()
                WHERE incident_no = %s
                """,
                (status, workflow_phase, current_recovery_state, age_seconds, resolution_type, incident_no),
            )
        conn.commit()


def _seed_proposal(incident_no: str, *, status: str = "pending") -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM incidents WHERE incident_no = %s", (incident_no,))
            incident = cur.fetchone()
            assert incident is not None
            incident_id = incident["id"]
            cur.execute(
                """
                INSERT INTO proposals (
                    incident_id,
                    title,
                    rationale,
                    target_devices,
                    commands,
                    rollback_commands,
                    verification_commands,
                    status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    incident_id,
                    "Restore interface",
                    "Synthetic proposal for recovery-state tests.",
                    Json(["HQ-CORE-RT01"]),
                    Json(["interface Gi0/0", "no shutdown"]),
                    Json(["interface Gi0/0", "shutdown"]),
                    Json(["show interface Gi0/0"]),
                    status,
                ),
            )
            proposal_id = cur.fetchone()["id"]
            cur.execute(
                "UPDATE incidents SET current_proposal_id = %s WHERE id = %s",
                (proposal_id, incident_id),
            )
        conn.commit()
    return proposal_id


def _seed_system_restart_recovering(incident_no: str, *, age_seconds: int) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM incidents WHERE incident_no = %s", (incident_no,))
            incident = cur.fetchone()
            assert incident is not None
            incident_id = incident["id"]
            cur.execute("SELECT id FROM devices WHERE hostname = 'LAB-MGMT-BR01'")
            device = cur.fetchone()
            device_id = device["id"] if device else None

            boot_logs = [
                (
                    "<189>33: *Mar 27 14:15:05.043: %PLATFORM-5-SIGNATURE_VERIFIED: Image 'flash0:/vios-adventerprisek9-m' passed code signing verification",
                    "SIGNATURE_VERIFIED",
                ),
                (
                    "<190>31: *Mar 27 14:14:28.207: %SYS-6-LOGGINGHOST_STARTSTOP: Logging to host 192.168.1.203 port 514 started - CLI initiated",
                    "LOGGINGHOST_STARTSTOP",
                ),
            ]

            for raw_message, mnemonic in boot_logs:
                cur.execute(
                    """
                    INSERT INTO raw_logs (source_ip, hostname, raw_message, event_time, parse_status, metadata)
                    VALUES (%s, %s, %s, NOW() - (interval '1 second' * %s), 'llm_decided', %s::jsonb)
                    RETURNING id
                    """,
                    ("10.255.0.1", "LAB-MGMT-BR01", raw_message, age_seconds, Json({"source": "syslog"})),
                )
                raw_log_id = cur.fetchone()["id"]
                cur.execute(
                    """
                    INSERT INTO events (
                        raw_log_id, device_id, event_family, event_state, severity,
                        title, summary, correlation_key, metadata
                    )
                    VALUES (%s, %s, 'system', 'info', 'info', %s, %s, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (
                        raw_log_id,
                        device_id,
                        f"System info on LAB-MGMT-BR01 ({mnemonic})",
                        raw_message,
                        "10.255.0.1|system",
                        Json({"mnemonic": mnemonic}),
                    ),
                )
                event_id = cur.fetchone()["id"]
                cur.execute(
                    """
                    INSERT INTO incident_events (incident_id, event_id)
                    VALUES (%s, %s)
                    ON CONFLICT (incident_id, event_id) DO NOTHING
                    """,
                    (incident_id, event_id),
                )

            cur.execute(
                """
                UPDATE incidents
                SET title = 'System Restart on LAB-MGMT-BR01 (MGMT)',
                    summary = 'Boot sequence underway after system restart.',
                    probable_cause = 'System restart awaiting post-boot monitoring.',
                    primary_source_ip = '10.255.0.1',
                    event_family = 'system',
                    status = 'recovering',
                    current_recovery_state = 'signal_detected',
                    last_seen_at = NOW() - (interval '1 second' * %s),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (age_seconds, incident_id),
            )
            cur.execute(
                "SELECT COUNT(*) AS count FROM incident_events WHERE incident_id = %s",
                (incident_id,),
            )
            event_count = cur.fetchone()["count"]
            cur.execute(
                "UPDATE incidents SET event_count = %s WHERE id = %s",
                (event_count, incident_id),
            )
        conn.commit()


def test_verify_recovery_moves_healed_incident_to_monitoring():
    with TestClient(app) as client:
        incident_no = _create_incident(client)
        _seed_status(
            incident_no,
            status="active",
            workflow_phase="approved_to_execute",
            current_recovery_state="signal_detected",
        )

        response = client.post(
            f"/api/aiops/incidents/{incident_no}/verify",
            json={"healed": True, "note": "Service looks healthy again; keep it under watch."},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["incident"]["status"] == "monitoring"
        assert payload["incident"]["workflow_phase"] == "none"
        assert payload["incident"]["current_recovery_state"] == "monitoring"
        assert payload["incident"]["resolution_type"] == "verified_recovery"


def test_verify_recovery_moves_not_healed_incident_back_to_active():
    with TestClient(app) as client:
        incident_no = _create_incident(client)
        _seed_status(
            incident_no,
            status="active",
            workflow_phase="approved_to_execute",
            current_recovery_state="signal_detected",
            resolution_type="verified_recovery",
        )

        response = client.post(
            f"/api/aiops/incidents/{incident_no}/verify",
            json={"healed": False, "note": "Still broken; continue the investigation."},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["incident"]["status"] == "active"
        assert payload["incident"]["workflow_phase"] == "none"
        assert payload["incident"]["current_recovery_state"] == "watching"


def test_explicit_recovery_signal_resolves_incident_immediately():
    with TestClient(app) as client:
        incident_no = _create_incident(client)

        response = client.post("/api/aiops/logs/ingest", json=RECOVERY_PAYLOAD)
        assert response.status_code == 200

        detail = client.get(f"/api/aiops/incidents/{incident_no}").json()
        assert detail["incident"]["status"] == "resolved"
        assert detail["incident"]["workflow_phase"] == "none"


def test_auto_resolve_requires_monitoring_and_preserves_resolution_type():
    with TestClient(app) as client:
        incident_no = _create_incident(client)
        _seed_status(
            incident_no,
            status="monitoring",
            current_recovery_state="monitoring",
            age_seconds=600,
            resolution_type="verified_recovery",
        )

        assert _aiops_service._auto_resolve_stable_incidents() == 1

        resolved_detail = client.get(f"/api/aiops/incidents/{incident_no}").json()
        assert resolved_detail["incident"]["status"] == "resolved"
        assert resolved_detail["incident"]["resolution_type"] == "verified_recovery"


def test_monitoring_incident_with_remediation_available_auto_resolves_and_cancels_proposal():
    with TestClient(app) as client:
        incident_no = _create_incident(client)
        _seed_status(
            incident_no,
            status="monitoring",
            workflow_phase="remediation_available",
            current_recovery_state="monitoring",
            age_seconds=600,
            resolution_type="verified_recovery",
        )
        proposal_id = _seed_proposal(incident_no, status="pending")

        detail = client.get(f"/api/aiops/incidents/{incident_no}").json()

        assert detail["incident"]["status"] == "resolved"
        assert detail["incident"]["workflow_phase"] == "none"
        assert detail["proposal"]["id"] == proposal_id
        assert detail["proposal"]["status"] == "cancelled"
        assert detail["proposal"]["cancelled_reason"] == "incident_auto_resolved"


def test_related_incidents_resolve_independently_without_parent_rollup():
    with TestClient(app) as client:
        owner, peer = _create_linked_incidents(client)
        _seed_status(owner["incident_no"], status="active", current_recovery_state="watching", age_seconds=10)
        _seed_status(peer["incident_no"], status="monitoring", current_recovery_state="monitoring", age_seconds=600)

        peer_detail = client.get(f"/api/aiops/incidents/{peer['incident_no']}").json()
        owner_detail = client.get(f"/api/aiops/incidents/{owner['incident_no']}").json()

        assert peer_detail["incident"]["status"] == "resolved"
        assert owner_detail["incident"]["status"] == "active"


def test_system_restart_auto_moves_to_monitoring_when_boot_progress_exists():
    with patch.dict(
        "os.environ",
        {
            "AIOPS_SYSTEM_BOOT_MONITORING_SECONDS": "1",
            "AIOPS_RECOVERY_STABILITY_SECONDS": "300",
        },
    ):
        with TestClient(app) as client:
            incident_no = _create_incident(client)
            _seed_system_restart_recovering(incident_no, age_seconds=120)

            detail = client.get(f"/api/aiops/incidents/{incident_no}").json()

            assert detail["incident"]["status"] == "monitoring"
            assert detail["incident"]["current_recovery_state"] == "monitoring"


def test_get_incident_refreshes_time_based_resolution_without_new_logs():
    with TestClient(app) as client:
        incident_no = _create_incident(client)
        _seed_status(
            incident_no,
            status="monitoring",
            current_recovery_state="monitoring",
            age_seconds=600,
            resolution_type="verified_recovery",
        )

        detail = client.get(f"/api/aiops/incidents/{incident_no}").json()

        assert detail["incident"]["status"] == "resolved"
        assert detail["incident"]["resolution_type"] == "verified_recovery"
