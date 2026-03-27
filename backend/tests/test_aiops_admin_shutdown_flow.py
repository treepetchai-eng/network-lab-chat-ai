from __future__ import annotations

import time

from fastapi.testclient import TestClient

from src.aiops.parser import parse_syslog
from src.api import app


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


def _create_linked_admin_down_incidents(client: TestClient) -> tuple[dict, dict]:
    reset_before = client.post("/api/aiops/incidents/reset")
    assert reset_before.status_code == 200

    first = client.post("/api/aiops/logs/ingest", json=ADMIN_DOWN_PAYLOAD)
    assert first.status_code == 200

    second = client.post("/api/aiops/logs/ingest", json=REMOTE_BGP_DOWN_PAYLOAD)
    assert second.status_code == 200

    incidents: list[dict] = []
    for _ in range(20):
        incidents_response = client.get("/api/aiops/incidents")
        assert incidents_response.status_code == 200
        incidents = incidents_response.json()
        if len(incidents) >= 2:
            break
        time.sleep(0.1)

    assert len(incidents) == 2
    owner = next(incident for incident in incidents if incident["workflow_phase"] == "intent_confirmation_required")
    peer = next(incident for incident in incidents if incident["incident_no"] != owner["incident_no"])
    return owner, peer


def test_parse_syslog_keeps_admin_down_as_non_standalone_evidence():
    parsed = parse_syslog(
        source_ip="10.255.1.11",
        hostname="HQ-CORE-RT01",
        raw_message=ADMIN_DOWN_PAYLOAD["raw_message"],
    )

    assert parsed is not None
    assert parsed["event_state"] == "admin_down"
    assert parsed["metadata"]["operator_initiated_hint"] is True
    assert parsed["metadata"]["eligible_for_standalone_incident"] is False


def test_linked_admin_down_creates_owner_and_related_peer_incidents():
    with TestClient(app) as client:
        owner, peer = _create_linked_admin_down_incidents(client)

        assert owner["status"] == "active"
        assert owner["category"] == "config-related"
        assert owner["metadata"]["intent_status"] == "needs_confirmation"
        assert owner["remediation_owner_incident_id"] == owner["id"]
        assert owner["child_count"] == 1

        assert peer["status"] == "active"
        assert peer["workflow_phase"] == "none"
        assert peer["category"] == "config-related"
        assert peer["remediation_owner_incident_id"] == owner["id"]
        assert peer["relation_group_key"] == owner["relation_group_key"]

        owner_detail = client.get(f"/api/aiops/incidents/{owner['incident_no']}")
        assert owner_detail.status_code == 200
        owner_payload = owner_detail.json()
        assert owner_payload["proposal"] is None
        assert owner_payload["remediation_owner_incident"] is None
        assert len(owner_payload["related_incidents"]) == 1
        assert owner_payload["related_incidents"][0]["incident"]["incident_no"] == peer["incident_no"]
        assert owner_payload["related_incidents"][0]["owns_remediation"] is False

        peer_detail = client.get(f"/api/aiops/incidents/{peer['incident_no']}")
        assert peer_detail.status_code == 200
        peer_payload = peer_detail.json()
        assert peer_payload["proposal"] is None
        assert peer_payload["remediation_owner_incident"]["incident_no"] == owner["incident_no"]
        assert len(peer_payload["related_incidents"]) == 1
        assert peer_payload["related_incidents"][0]["incident"]["incident_no"] == owner["incident_no"]
        assert peer_payload["related_incidents"][0]["owns_remediation"] is True


def test_confirm_intentional_shutdown_resolves_owner_and_peer_incidents():
    with TestClient(app) as client:
        owner, peer = _create_linked_admin_down_incidents(client)

        response = client.post(
            f"/api/aiops/incidents/{owner['incident_no']}/intent",
            json={
                "intent": "intentional",
                "note": "Planned maintenance shutdown on HQ-CORE-RT01 GigabitEthernet0/0.",
                "actor": "netops",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["incident"]["status"] == "resolved"
        assert payload["incident"]["workflow_phase"] == "none"
        assert payload["incident"]["resolution_type"] == "confirmed_intentional_shutdown"
        assert payload["proposal"] is None

        peer_detail = client.get(f"/api/aiops/incidents/{peer['incident_no']}")
        assert peer_detail.status_code == 200
        assert peer_detail.json()["incident"]["status"] == "resolved"


def test_confirm_unintentional_shutdown_creates_proposal_only_on_owner_incident():
    with TestClient(app) as client:
        owner, peer = _create_linked_admin_down_incidents(client)

        response = client.post(
            f"/api/aiops/incidents/{owner['incident_no']}/intent",
            json={
                "intent": "unintentional",
                "note": "Operator confirmed the shutdown was accidental.",
                "actor": "netops",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["incident"]["status"] == "active"
        assert payload["incident"]["workflow_phase"] == "remediation_available"
        assert payload["incident"]["metadata"]["intent_status"] == "confirmed_unintentional"
        assert payload["proposal"] is not None
        assert payload["proposal"]["target_devices"] == ["HQ-CORE-RT01"]
        assert payload["proposal"]["commands"] == ["interface GigabitEthernet0/0", "no shutdown"]

        peer_detail = client.get(f"/api/aiops/incidents/{peer['incident_no']}")
        assert peer_detail.status_code == 200
        peer_payload = peer_detail.json()
        assert peer_payload["proposal"] is None
        assert peer_payload["remediation_owner_incident"]["incident_no"] == owner["incident_no"]


def test_approve_and_execute_positive_verification_resolves_owner_incident(monkeypatch):
    monkeypatch.setattr("src.aiops.service.execute_config", lambda *_args, **_kwargs: "[CONFIG APPLIED] interface restored")
    monkeypatch.setattr(
        "src.aiops.service.run_show_commands",
        lambda *_args, **_kwargs: "GigabitEthernet0/0 is up, line protocol is up",
    )

    with TestClient(app) as client:
        owner, _peer = _create_linked_admin_down_incidents(client)
        confirmed = client.post(
            f"/api/aiops/incidents/{owner['incident_no']}/intent",
            json={
                "intent": "unintentional",
                "note": "Operator confirmed the shutdown was accidental.",
                "actor": "netops",
            },
        )
        assert confirmed.status_code == 200

        approved = client.post(
            f"/api/aiops/incidents/{owner['incident_no']}/approve",
            json={"actor": "netops"},
        )
        assert approved.status_code == 200
        approved_payload = approved.json()
        assert approved_payload["incident"]["status"] == "active"
        assert approved_payload["incident"]["workflow_phase"] == "approved_to_execute"
        assert approved_payload["proposal"]["status"] == "approved"

        executed = client.post(
            f"/api/aiops/incidents/{owner['incident_no']}/execute",
            json={"actor": "netops"},
        )
        assert executed.status_code == 200
        executed_payload = executed.json()
        assert executed_payload["incident"]["status"] == "resolved"
        assert executed_payload["incident"]["workflow_phase"] == "none"
        assert executed_payload["incident"]["resolution_type"] == "verified_recovery"
        assert executed_payload["proposal"]["status"] == "executed"
        assert executed_payload["execution"]["status"] == "completed"
