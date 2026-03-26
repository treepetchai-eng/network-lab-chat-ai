from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage, ToolMessage

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src.api import app
from src.session_manager import delete_session, get_session


def test_create_incident_session_preloads_context_and_device_cache(monkeypatch):
    from src import api as api_module

    incident_no = "INC-000123"
    detail = {
        "incident": {
            "incident_no": incident_no,
            "severity": "p2",
            "status": "investigating",
            "title": "BGP adjacency down on HQ core",
            "primary_hostname": "HQ-CORE-RT01",
            "primary_source_ip": "10.255.1.11",
            "os_platform": "cisco_ios",
            "device_role": "router",
            "event_family": "bgp",
            "correlation_key": "bgp|HQ-CORE-RT01|10.255.254.2",
            "site": "HQ",
            "version": "15.6(2)T",
        },
        "ai_summary": {
            "summary": "BGP peer 10.255.254.2 dropped unexpectedly after stable operation.",
        },
        "troubleshoot": {
            "summary": "Assessment: SSH connectivity to HQ-CORE-RT01 failed before BGP validation.",
            "conclusion": "Engineering judgment: restore SSH reachability first, then validate the BGP peer.",
            "steps": [
                {
                    "tool_name": "run_cli",
                    "args": {"command": "show ip bgp summary"},
                    "content": "Neighbor 10.255.254.2 Idle",
                }
            ],
        },
        "raw_logs": [
            {"raw_message": "%BGP-5-ADJCHANGE: neighbor 10.255.254.2 Down BGP Notification"},
        ],
    }

    monkeypatch.setattr(api_module, "_ensure_aiops_ready", lambda: None)
    monkeypatch.setattr(api_module._aiops_service, "get_incident", lambda no: detail if no == incident_no else None)

    with TestClient(app) as client:
        response = client.post(f"/api/session/incident/{incident_no}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"]

    session = asyncio.run(get_session(payload["session_id"]))
    assert session is not None
    assert incident_no in session.incident_context
    assert "HQ-CORE-RT01" in session.incident_context
    assert "show ip bgp summary" in session.incident_context
    assert "BGP Notification" in session.incident_context
    assert "Recorded troubleshoot evidence may already answer follow-up questions" in session.incident_context
    assert session.device_cache["HQ-CORE-RT01"]["ip_address"] == "10.255.1.11"
    assert session.device_cache["HQ-CORE-RT01"]["os_platform"] == "cisco_ios"
    assert session.device_cache["HQ-CORE-RT01"]["site"] == "HQ"
    assert isinstance(session.preloaded_messages[0], HumanMessage)
    assert "Recorded troubleshoot summary" in session.preloaded_messages[0].content
    assert any(isinstance(message, ToolMessage) for message in session.preloaded_messages[1:])

    assert asyncio.run(delete_session(payload["session_id"])) is True
