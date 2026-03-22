from __future__ import annotations

from fastapi.testclient import TestClient

from src.api import app


def test_chat_api_starts_and_can_create_session():
    with TestClient(app) as client:
        response = client.post("/api/session")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"]
