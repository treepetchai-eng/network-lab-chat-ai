from __future__ import annotations

import time

from fastapi.testclient import TestClient

from src.api import app


def test_aiops_ingest_creates_incident_and_exposes_detail():
    payload = {
        "source_ip": "10.255.1.11",
        "hostname": "HQ-CORE-RT01",
        "raw_message": "%BGP-5-ADJCHANGE: neighbor 10.255.254.2 Down BGP Notification",
    }

    with TestClient(app) as client:
        reset_before = client.post("/api/aiops/incidents/reset")
        assert reset_before.status_code == 200

        ingest = client.post("/api/aiops/logs/ingest", json=payload)
        assert ingest.status_code == 200
        detail = ingest.json()
        assert detail["accepted"] is True

        incident_no = None
        for _ in range(30):
            incidents_response = client.get("/api/aiops/incidents")
            assert incidents_response.status_code == 200
            incidents = incidents_response.json()
            if incidents:
                incident_no = incidents[0]["incident_no"]
                break
            time.sleep(0.1)
        assert incident_no is not None
        assert incident_no.startswith("INC-")

        incident_response = client.get(f"/api/aiops/incidents/{incident_no}")
        assert incident_response.status_code == 200
        incident_payload = incident_response.json()
        assert incident_payload["incident"]["event_family"] == "bgp"
        assert incident_payload["incident"]["status"] == "active"
        assert incident_payload["incident"]["workflow_phase"] == "none"
        assert incident_payload["raw_logs"]

        logs_response = client.get("/api/aiops/logs", params={"incident_no": incident_no})
        assert logs_response.status_code == 200
        logs_payload = logs_response.json()
        assert logs_payload["raw_logs"]
        assert logs_payload["events"]

        reset = client.post("/api/aiops/incidents/reset")
        assert reset.status_code == 200
        reset_payload = reset.json()
        assert reset_payload["incidents_removed"] >= 1

        verify = client.post(
            f"/api/aiops/incidents/{incident_no}/verify",
            json={"healed": False, "note": "Still monitoring after the initial recovery review."},
        )
        assert verify.status_code == 404

        incidents_after_reset = client.get("/api/aiops/incidents")
        assert incidents_after_reset.status_code == 200
        assert incidents_after_reset.json() == []


def test_aiops_incident_detail_resolves_raw_log_hostname_from_inventory():
    payload = {
        "source_ip": "10.255.1.11",
        "hostname": "10.255.1.11",
        "raw_message": "%BGP-5-ADJCHANGE: neighbor 10.255.254.2 Down BGP Notification",
    }

    with TestClient(app) as client:
        reset_before = client.post("/api/aiops/incidents/reset")
        assert reset_before.status_code == 200

        ingest = client.post("/api/aiops/logs/ingest", json=payload)
        assert ingest.status_code == 200

        incident_no = None
        for _ in range(30):
            incidents_response = client.get("/api/aiops/incidents")
            assert incidents_response.status_code == 200
            incidents = incidents_response.json()
            if incidents:
                incident_no = incidents[0]["incident_no"]
                break
            time.sleep(0.1)
        assert incident_no is not None

        incident_response = client.get(f"/api/aiops/incidents/{incident_no}")
        assert incident_response.status_code == 200
        incident_payload = incident_response.json()
        assert incident_payload["raw_logs"][0]["hostname"] == "HQ-CORE-RT01"

        logs_response = client.get("/api/aiops/logs", params={"incident_no": incident_no})
        assert logs_response.status_code == 200
        logs_payload = logs_response.json()
        assert logs_payload["raw_logs"][0]["hostname"] == "HQ-CORE-RT01"
