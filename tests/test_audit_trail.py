from __future__ import annotations

from datetime import datetime

from app.domain.models import AuditEvent
from app.repositories.audit import InMemoryAuditRepository
from tests.support import (
    APPROVER_A,
    OPERATOR_A,
    OPERATOR_B,
    SENSITIVE_VALUES,
    VIEWER_A,
    actor,
    calendar_body,
    captured_app_logs,
    create_workflow,
    draft_body,
    execute_tool,
    make_client,
    task_body,
)

AUDIT_FIELD_ALLOWLIST = {
    "event_id",
    "occurred_at",
    "correlation_id",
    "tenant_id",
    "actor_id",
    "workflow_id",
    "event_type",
    "outcome",
    "tool_name",
    "execution_id",
    "idempotency_key_hash",
    "attempt",
    "error_category",
}


def _run_scenario(client) -> str:
    """Week 2 demo flow: allowed, denied, blocked, approved, executed, replayed, conflict."""
    workflow_id = create_workflow(client, **{"X-Correlation-ID": "cid-create"})
    execute_tool(client, draft_body(workflow_id), "audit-draft-001", **{"X-Correlation-ID": "c1"})
    execute_tool(client, calendar_body(workflow_id), "audit-view-0001", VIEWER_A)
    execute_tool(client, task_body(workflow_id), "audit-task-0001")
    client.post(
        f"/api/v1/workflows/{workflow_id}/approval",
        json={"decision": "approve", "comment": "ok"},
        headers=actor(APPROVER_A, **{"X-Correlation-ID": "cid-approve"}),
    )
    execute_tool(client, task_body(workflow_id), "audit-task-0001")
    execute_tool(client, task_body(workflow_id), "audit-task-0001")
    execute_tool(client, task_body(workflow_id, 48), "audit-task-0001")
    return workflow_id


def test_audit_trail_is_chronological_with_expected_events() -> None:
    client = make_client()
    workflow_id = _run_scenario(client)

    response = client.get(f"/api/v1/workflows/{workflow_id}/audit", headers=actor(OPERATOR_A))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow_id"] == workflow_id
    events = body["events"]
    summary = [(e["event_type"], e["outcome"], e["actor_id"]) for e in events]
    assert summary == [
        ("workflow_created", "succeeded", OPERATOR_A),
        ("tool_execution_started", "started", OPERATOR_A),
        ("tool_execution_succeeded", "succeeded", OPERATOR_A),
        ("tool_execution_denied", "denied", VIEWER_A),
        ("tool_execution_blocked_approval", "blocked", OPERATOR_A),
        ("workflow_approved", "succeeded", APPROVER_A),
        ("tool_execution_started", "started", OPERATOR_A),
        ("tool_execution_succeeded", "succeeded", OPERATOR_A),
        ("tool_execution_replayed", "replayed", OPERATOR_A),
        ("idempotency_conflict", "conflict", OPERATOR_A),
    ]
    timestamps = [datetime.fromisoformat(e["occurred_at"]) for e in events]
    assert timestamps == sorted(timestamps)
    assert all(ts.tzinfo is not None for ts in timestamps)
    assert all(e["tenant_id"] == "tenant-a" for e in events)

    assert events[0]["correlation_id"] == "cid-create"
    assert events[1]["correlation_id"] == "c1"
    assert events[1]["tool_name"] == "messaging.create_follow_up_draft"
    assert events[1]["attempt"] == 1
    assert events[5]["correlation_id"] == "cid-approve"
    assert events[7]["execution_id"] == events[8]["execution_id"]
    assert events[9]["error_category"] == "idempotency_conflict"
    assert events[1]["idempotency_key_hash"].startswith("sha256:")


def test_audit_trail_is_tenant_protected() -> None:
    client = make_client()
    workflow_id = _run_scenario(client)
    response = client.get(f"/api/v1/workflows/{workflow_id}/audit", headers=actor(OPERATOR_B))
    assert response.status_code == 404
    assert "tenant-a" not in response.text


def test_other_tenant_audit_only_contains_its_own_events() -> None:
    client = make_client()
    _run_scenario(client)
    workflow_b = create_workflow(client, OPERATOR_B)
    events = client.get(f"/api/v1/workflows/{workflow_b}/audit", headers=actor(OPERATOR_B)).json()[
        "events"
    ]
    assert [e["event_type"] for e in events] == ["workflow_created"]
    assert events[0]["tenant_id"] == "tenant-b"


def test_audit_and_logs_contain_no_sensitive_data() -> None:
    client = make_client()
    with captured_app_logs() as logs:
        workflow_id = _run_scenario(client)
        workflow = client.get(f"/api/v1/workflows/{workflow_id}", headers=actor(OPERATOR_A))
        audit = client.get(f"/api/v1/workflows/{workflow_id}/audit", headers=actor(OPERATOR_A))

    draft_text = workflow.json()["proposal"]["message_draft"]
    log_text = "\n".join(logs)
    assert logs, "expected application logs to be captured"
    for text in (audit.text, log_text):
        for value in (*SENSITIVE_VALUES, draft_text):
            assert value not in text
        assert "Traceback" not in text
        assert "audit-task-0001" not in text
        assert "due_in_hours" not in text
    assert '"actor_id": "tenant-a-operator"' in log_text
    assert '"tool_name": "crm.create_follow_up_task"' in log_text


def test_audit_event_schema_is_a_closed_allowlist() -> None:
    assert set(AuditEvent.model_fields) == AUDIT_FIELD_ALLOWLIST
    assert AuditEvent.model_config.get("extra") == "forbid"
    assert AuditEvent.model_config.get("frozen") is True


def test_audit_repository_is_append_only() -> None:
    repo = InMemoryAuditRepository()
    public = {name for name in dir(repo) if not name.startswith("_")}
    assert public == {"append", "list_for_workflow"}
