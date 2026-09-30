from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import build_app

DEMO_PAYLOAD = {
    "lead_id": "lead-approval-001",
    "first_name": "Avery",
    "last_name": "Stone",
    "email": "avery.stone@example.com",
    "phone": "+1 (555) 010-0101",
    "preferred_city": "Austin",
    "budget_max": 650000,
    "min_bedrooms": 3,
    "message": "Family home tour request.",
}


def _client() -> TestClient:
    return TestClient(build_app())


def _create_workflow(client: TestClient) -> str:
    response = client.post("/api/v1/leads/webhook", json=DEMO_PAYLOAD)
    assert response.status_code == 200, response.text
    return str(response.json()["workflow"]["workflow_id"])


def test_get_workflow_by_id_succeeds() -> None:
    client = _client()
    workflow_id = _create_workflow(client)
    response = client.get(f"/api/v1/workflows/{workflow_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow_id"] == workflow_id
    assert body["state"] == "PENDING_APPROVAL"
    assert body["side_effect_executed"] is False
    assert body["proposal"]["requires_human_approval"] is True


def test_get_unknown_workflow_returns_404() -> None:
    client = _client()
    response = client.get(f"/api/v1/workflows/{uuid4()}")
    assert response.status_code == 404


def test_approve_changes_state_and_keeps_no_side_effect() -> None:
    client = _client()
    workflow_id = _create_workflow(client)
    response = client.post(
        f"/api/v1/workflows/{workflow_id}/approval",
        json={
            "decision": "approve",
            "approved_by": "demo-operator",
            "comment": "Review completed in local demonstration",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "APPROVED"
    assert body["side_effect_executed"] is False
    assert body["approval"]["approved_by"] == "demo-operator"


def test_reject_changes_state_and_keeps_no_side_effect() -> None:
    client = _client()
    workflow_id = _create_workflow(client)
    response = client.post(
        f"/api/v1/workflows/{workflow_id}/approval",
        json={
            "decision": "reject",
            "approved_by": "demo-operator",
            "comment": "Rejected as demonstration",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "REJECTED"
    assert body["side_effect_executed"] is False


def test_second_approval_returns_409() -> None:
    client = _client()
    workflow_id = _create_workflow(client)
    first = client.post(
        f"/api/v1/workflows/{workflow_id}/approval",
        json={
            "decision": "approve",
            "approved_by": "demo-operator",
        },
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/v1/workflows/{workflow_id}/approval",
        json={
            "decision": "approve",
            "approved_by": "demo-operator-2",
        },
    )
    assert second.status_code == 409


def test_second_rejection_after_approve_returns_409() -> None:
    client = _client()
    workflow_id = _create_workflow(client)
    first = client.post(
        f"/api/v1/workflows/{workflow_id}/approval",
        json={
            "decision": "reject",
            "approved_by": "demo-operator",
        },
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/v1/workflows/{workflow_id}/approval",
        json={
            "decision": "approve",
            "approved_by": "demo-operator-2",
        },
    )
    assert second.status_code == 409
