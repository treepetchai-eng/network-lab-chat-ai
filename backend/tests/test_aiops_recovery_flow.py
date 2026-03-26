from __future__ import annotations

import time

from fastapi.testclient import TestClient

from src.aiops.db import connect
from src.api import _aiops_service, app

PAYLOAD = {
    "source_ip": "10.255.1.11",
    "hostname": "HQ-CORE-RT01",
    "raw_message": "%BGP-5-ADJCHANGE: neighbor 10.255.254.2 Down BGP Notification",
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


def _seed_status(
    incident_no: str,
    *,
    status: str,
    current_recovery_state: str,
    age_seconds: int = 0,
    resolution_type: str | None = None,
) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE incidents
                SET status = %s,
                    current_recovery_state = %s,
                    last_seen_at = NOW() - (interval '1 second' * %s),
                    resolution_type = %s,
                    resolved_at = NULL,
                    updated_at = NOW()
                WHERE incident_no = %s
                """,
                (status, current_recovery_state, age_seconds, resolution_type, incident_no),
            )
        conn.commit()


def test_verify_recovery_moves_healed_incident_to_monitoring():
    with TestClient(app) as client:
        incident_no = _create_incident(client)
        _seed_status(incident_no, status="verifying", current_recovery_state="signal_detected")

        response = client.post(
            f"/api/aiops/incidents/{incident_no}/verify",
            json={"healed": True, "note": "Service looks healthy again; keep it under watch."},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["incident"]["status"] == "monitoring"
        assert payload["incident"]["current_recovery_state"] == "monitoring"
        assert payload["incident"]["resolved_at"] is None
        assert payload["incident"]["resolution_type"] == "verified_recovery"


def test_verify_recovery_moves_not_healed_incident_back_to_investigating():
    with TestClient(app) as client:
        incident_no = _create_incident(client)
        _seed_status(
            incident_no,
            status="verifying",
            current_recovery_state="signal_detected",
            resolution_type="verified_recovery",
        )

        response = client.post(
            f"/api/aiops/incidents/{incident_no}/verify",
            json={"healed": False, "note": "Still broken; continue the investigation."},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["incident"]["status"] == "investigating"
        assert payload["incident"]["current_recovery_state"] == "watching"
        assert payload["incident"]["resolved_at"] is None
        assert payload["incident"]["resolution_type"] is None


def test_auto_resolve_requires_monitoring_and_preserves_resolution_type():
    with TestClient(app) as client:
        incident_no = _create_incident(client)
        _seed_status(incident_no, status="recovering", current_recovery_state="signal_detected", age_seconds=600)

        assert _aiops_service._auto_resolve_stable_incidents() == 0

        recovering_detail = client.get(f"/api/aiops/incidents/{incident_no}").json()
        assert recovering_detail["incident"]["status"] == "recovering"

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
        assert resolved_detail["incident"]["resolved_at"] is not None
