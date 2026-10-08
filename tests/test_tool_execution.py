from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from tests.support import (
    APPROVER_A,
    OPERATOR_A,
    SENSITIVE_VALUES,
    actor,
    approve,
    calendar_body,
    captured_app_logs,
    context_body,
    counting_adapters,
    create_workflow,
    draft_body,
    execute_tool,
    make_client,
    task_body,
)


def _assert_simulated(body: dict[str, Any]) -> None:
    assert body["simulated"] is True
    assert body["external_side_effect"] is False
    assert body["idempotency_replayed"] is False
    assert body["status"] == "SUCCEEDED"
    assert body["attempts"] == 1
    UUID(body["execution_id"])


def test_calendar_availability_is_deterministic_and_books_nothing() -> None:
    client = make_client()
    workflow_id = create_workflow(client)

    first = execute_tool(client, calendar_body(workflow_id), "calendar-key-001")
    second = execute_tool(client, calendar_body(workflow_id), "calendar-key-002")

    assert first.status_code == 201, first.text
    body = first.json()
    _assert_simulated(body)
    result = body["result"]
    assert result["kind"] == "availability"
    assert result["booking_created"] is False
    assert result["timezone"] == "UTC"
    assert [slot["start"] for slot in result["slots"]] == [
        "2030-03-04T09:00:00Z",
        "2030-03-04T11:00:00Z",
        "2030-03-04T14:00:00Z",
        "2030-03-04T16:00:00Z",
    ]
    assert second.json()["result"] == result
    assert first.headers["X-Correlation-ID"]


def test_calendar_returns_no_slots_on_weekends() -> None:
    client = make_client()
    workflow_id = create_workflow(client)
    response = execute_tool(client, calendar_body(workflow_id, "2030-03-09"), "calendar-weekend")
    assert response.status_code == 201, response.text
    assert response.json()["result"]["slots"] == []


def test_messaging_creates_unsent_draft_without_exposing_lead_data() -> None:
    adapters, crm, calendar, messaging = counting_adapters()
    client = make_client(tool_adapters=adapters)
    with captured_app_logs() as logs:
        workflow_id = create_workflow(client)
        response = execute_tool(client, draft_body(workflow_id), "draft-key-0001")
        audit = client.get(f"/api/v1/workflows/{workflow_id}/audit", headers=actor(OPERATOR_A))

    assert response.status_code == 201, response.text
    body = response.json()
    _assert_simulated(body)
    assert body["result"]["status"] == "DRAFT"
    assert body["result"]["sent"] is False
    assert body["result"]["channel"] == "email"

    drafts = messaging.list_drafts("tenant-a", UUID(workflow_id))
    assert len(drafts) == 1
    assert drafts[0].status == "DRAFT"
    assert drafts[0].sent is False
    assert drafts[0].simulated is True
    assert body["result"]["draft_id"] == drafts[0].draft_id

    draft_text = drafts[0].body
    exposed = response.text + audit.text + "\n".join(logs)
    for value in (*SENSITIVE_VALUES, draft_text):
        assert value not in exposed


def test_lead_context_contains_no_contact_details() -> None:
    client = make_client()
    workflow_id = create_workflow(client)
    response = execute_tool(client, context_body(workflow_id), "context-key-001")
    assert response.status_code == 201, response.text
    result = response.json()["result"]
    assert result["kind"] == "lead_context"
    assert result["lifecycle_stage"] == "new_inquiry"
    assert result["contact_details_included"] is False
    for value in SENSITIVE_VALUES:
        assert value not in response.text


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda b: b | {"recipient": "x@example.com"}, id="extra-field"),
        pytest.param(lambda b: b | {"due_in_hours": 0}, id="due-too-small"),
        pytest.param(lambda b: b | {"due_in_hours": 73}, id="due-too-large"),
        pytest.param(lambda b: b | {"due_in_hours": "24"}, id="due-as-string"),
        pytest.param(lambda b: b | {"due_in_hours": True}, id="due-as-bool"),
        pytest.param(lambda b: {k: v for k, v in b.items() if k != "due_in_hours"}, id="missing"),
        pytest.param(lambda b: b | {"workflow_id": "not-a-uuid"}, id="bad-workflow-id"),
    ],
)
def test_invalid_task_invocation_returns_422(mutate) -> None:
    adapters, crm, _, _ = counting_adapters()
    client = make_client(tool_adapters=adapters)
    workflow_id = create_workflow(client)
    approve(client, workflow_id)
    response = execute_tool(client, mutate(task_body(workflow_id)), "invalid-task-01")
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "request_validation_failed"
    assert crm.task_calls == 0


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"channel": "sms"}, id="channel-sms"),
        pytest.param({"channel": "email", "body": "custom text"}, id="custom-body"),
    ],
)
def test_invalid_draft_invocation_returns_422(body: dict[str, Any]) -> None:
    client = make_client()
    workflow_id = create_workflow(client)
    payload = {"tool_name": "messaging.create_follow_up_draft", "workflow_id": workflow_id, **body}
    assert execute_tool(client, payload, "invalid-draft-1").status_code == 422


@pytest.mark.parametrize(
    "duration", [0, 10, 125, 45.5], ids=["zero", "not-multiple", "too-long", "float"]
)
def test_invalid_calendar_duration_returns_422(duration: object) -> None:
    client = make_client()
    workflow_id = create_workflow(client)
    body = calendar_body(workflow_id) | {"duration_minutes": duration}
    assert execute_tool(client, body, "invalid-cal-01").status_code == 422


def test_calendar_date_out_of_bounds_returns_422() -> None:
    client = make_client()
    workflow_id = create_workflow(client)
    body = calendar_body(workflow_id, "1999-12-31")
    assert execute_tool(client, body, "invalid-cal-02").status_code == 422


@pytest.mark.parametrize("tool_name", ["shell.exec", "crm.delete_lead", "", None])
def test_unknown_tool_name_is_rejected_and_no_adapter_runs(tool_name: object) -> None:
    adapters, crm, calendar, messaging = counting_adapters()
    client = make_client(tool_adapters=adapters)
    workflow_id = create_workflow(client)
    response = execute_tool(
        client, {"tool_name": tool_name, "workflow_id": workflow_id}, "unknown-tool-01"
    )
    assert response.status_code == 422, response.text
    assert (crm.context_calls, crm.task_calls, calendar.calls, messaging.calls) == (0, 0, 0, 0)


def test_validation_error_does_not_echo_rejected_input() -> None:
    client = make_client()
    workflow_id = create_workflow(client)
    secret_like = "do-not-echo-this-value@example.com"
    body = draft_body(workflow_id) | {"recipient": secret_like}
    response = execute_tool(client, body, "echo-check-001")
    assert response.status_code == 422
    assert secret_like not in response.text


def test_unknown_workflow_returns_404() -> None:
    client = make_client()
    response = execute_tool(client, context_body(str(uuid4())), "unknown-wf-001")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "workflow_not_found"


# --- Human approval boundary ----------------------------------------------------


def test_high_impact_task_is_blocked_while_pending() -> None:
    adapters, crm, _, _ = counting_adapters()
    client = make_client(tool_adapters=adapters)
    workflow_id = create_workflow(client)

    response = execute_tool(client, task_body(workflow_id), "task-key-00001")

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "approval_required"
    assert detail["error_category"] == "approval_required"
    assert crm.task_calls == 0
    assert crm.list_tasks("tenant-a", UUID(workflow_id)) == []

    events = client.get(f"/api/v1/workflows/{workflow_id}/audit", headers=actor(OPERATOR_A)).json()[
        "events"
    ]
    blocked = [e for e in events if e["event_type"] == "tool_execution_blocked_approval"]
    assert len(blocked) == 1
    assert blocked[0]["outcome"] == "blocked"
    assert blocked[0]["tool_name"] == "crm.create_follow_up_task"


def test_high_impact_task_is_blocked_on_rejected_workflow() -> None:
    adapters, crm, _, _ = counting_adapters()
    client = make_client(tool_adapters=adapters)
    workflow_id = create_workflow(client)
    client.post(
        f"/api/v1/workflows/{workflow_id}/approval",
        json={"decision": "reject"},
        headers=actor(APPROVER_A),
    )
    response = execute_tool(client, task_body(workflow_id), "task-key-rejected")
    assert response.status_code == 409
    assert crm.task_calls == 0


def test_high_impact_task_runs_after_approval_as_simulation_only() -> None:
    adapters, crm, _, _ = counting_adapters()
    client = make_client(tool_adapters=adapters)
    workflow_id = create_workflow(client)
    assert execute_tool(client, task_body(workflow_id), "task-key-00002").status_code == 409

    approve(client, workflow_id)
    response = execute_tool(client, task_body(workflow_id), "task-key-00002")

    assert response.status_code == 201, response.text
    body = response.json()
    _assert_simulated(body)
    assert body["result"]["kind"] == "follow_up_task"
    assert body["result"]["status"] == "OPEN"

    tasks = crm.list_tasks("tenant-a", UUID(workflow_id))
    assert len(tasks) == 1
    assert tasks[0].simulated is True
    assert tasks[0].task_id == body["result"]["task_id"]

    workflow = client.get(f"/api/v1/workflows/{workflow_id}", headers=actor(OPERATOR_A)).json()
    assert workflow["state"] == "APPROVED"
    assert workflow["side_effect_executed"] is False


def test_tool_results_never_report_external_side_effects() -> None:
    client = make_client()
    workflow_id = create_workflow(client)
    approve(client, workflow_id)
    bodies = [
        context_body(workflow_id),
        calendar_body(workflow_id),
        draft_body(workflow_id),
        task_body(workflow_id),
    ]
    for index, body in enumerate(bodies):
        response = execute_tool(client, body, f"side-effect-check-{index}")
        assert response.status_code == 201, response.text
        assert json.loads(response.text)["external_side_effect"] is False
