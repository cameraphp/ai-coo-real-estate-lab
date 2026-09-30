from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import build_app


def _client() -> TestClient:
    return TestClient(build_app())


DEMO_PAYLOAD = {
    "lead_id": "lead-demo-001",
    "first_name": "Avery",
    "last_name": "Stone",
    "email": "avery.stone@example.com",
    "phone": "+1 (555) 010-0101",
    "preferred_city": "Austin",
    "budget_max": 650000,
    "min_bedrooms": 3,
    "message": "Looking for a family home near parks. I can tour this weekend.",
}


def test_webhook_happy_path_returns_pending_approval_and_correlation_id() -> None:
    client = _client()
    response = client.post("/api/v1/leads/webhook", json=DEMO_PAYLOAD)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "correlation_id" in body
    assert body["workflow"]["state"] == "PENDING_APPROVAL"
    assert body["workflow"]["side_effect_executed"] is False
    assert body["side_effect_executed"] is False
    assert body["retrieved_count"] > 0
    assert response.headers.get("X-Correlation-ID") == body["correlation_id"]


def test_webhook_invalid_payload_returns_422() -> None:
    client = _client()
    payload = dict(DEMO_PAYLOAD)
    payload["budget_max"] = -5
    response = client.post("/api/v1/leads/webhook", json=payload)
    assert response.status_code == 422


def test_webhook_no_contact_returns_400() -> None:
    client = _client()
    payload = dict(DEMO_PAYLOAD)
    payload["email"] = None
    payload["phone"] = None
    response = client.post("/api/v1/leads/webhook", json=payload)
    assert response.status_code == 400


def test_client_correlation_id_propagates() -> None:
    client = _client()
    cid = "client-supplied-correlation-123"
    response = client.post(
        "/api/v1/leads/webhook",
        json=DEMO_PAYLOAD,
        headers={"X-Correlation-ID": cid},
    )
    assert response.status_code == 200, response.text
    assert response.headers.get("X-Correlation-ID") == cid
    assert response.json()["correlation_id"] == cid


def test_missing_correlation_id_is_generated() -> None:
    client = _client()
    response = client.post("/api/v1/leads/webhook", json=DEMO_PAYLOAD)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["correlation_id"]
    assert isinstance(body["correlation_id"], str)
    assert len(body["correlation_id"]) > 0
