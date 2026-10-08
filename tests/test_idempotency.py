from __future__ import annotations

import threading
from uuid import UUID, uuid4

import pytest

from app.domain.models import (
    CalendarFindAvailabilityInvocation,
    LeadContextResult,
    LeadIntent,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolName,
    Urgency,
)
from app.repositories.idempotency import IdempotencyDecision, InMemoryIdempotencyRepository
from app.services.audit_service import hash_idempotency_key
from app.services.tool_execution_service import fingerprint_invocation
from tests.support import (
    OPERATOR_A,
    OPERATOR_B,
    actor,
    approve,
    calendar_body,
    counting_adapters,
    create_workflow,
    execute_tool,
    make_client,
    task_body,
)

# --- HTTP semantics -----------------------------------------------------------


def test_first_invocation_calls_adapter_once_and_is_not_replayed() -> None:
    adapters, _, calendar, _ = counting_adapters()
    client = make_client(tool_adapters=adapters)
    workflow_id = create_workflow(client)

    response = execute_tool(client, calendar_body(workflow_id), "idem-first-001")

    assert response.status_code == 201
    assert response.json()["idempotency_replayed"] is False
    assert calendar.calls == 1


def test_exact_replay_returns_same_execution_without_calling_adapter() -> None:
    adapters, crm, _, _ = counting_adapters()
    client = make_client(tool_adapters=adapters)
    workflow_id = create_workflow(client)
    approve(client, workflow_id)

    first = execute_tool(client, task_body(workflow_id), "idem-replay-001")
    replay = execute_tool(client, task_body(workflow_id), "idem-replay-001")

    assert first.status_code == 201, first.text
    assert replay.status_code == 200, replay.text
    first_body, replay_body = first.json(), replay.json()
    assert replay_body["execution_id"] == first_body["execution_id"]
    assert replay_body["idempotency_replayed"] is True
    assert replay_body["status"] == "REPLAYED"
    assert replay_body["result"] == first_body["result"]
    assert replay_body["external_side_effect"] is False
    assert crm.task_calls == 1
    assert len(crm.list_tasks("tenant-a", UUID(workflow_id))) == 1

    events = client.get(f"/api/v1/workflows/{workflow_id}/audit", headers=actor(OPERATOR_A)).json()[
        "events"
    ]
    replayed = [e for e in events if e["event_type"] == "tool_execution_replayed"]
    assert len(replayed) == 1
    assert replayed[0]["execution_id"] == first_body["execution_id"]


def test_same_key_with_changed_request_returns_409() -> None:
    adapters, crm, _, _ = counting_adapters()
    client = make_client(tool_adapters=adapters)
    workflow_id = create_workflow(client)
    approve(client, workflow_id)

    assert execute_tool(client, task_body(workflow_id, 24), "idem-conflict-1").status_code == 201
    conflict = execute_tool(client, task_body(workflow_id, 48), "idem-conflict-1")

    assert conflict.status_code == 409, conflict.text
    detail = conflict.json()["detail"]
    assert detail["error"] == "idempotency_key_reused"
    assert detail["error_category"] == "idempotency_conflict"
    assert crm.task_calls == 1


def test_same_key_with_different_tool_returns_409() -> None:
    client = make_client()
    workflow_id = create_workflow(client)
    assert execute_tool(client, calendar_body(workflow_id), "idem-conflict-2").status_code == 201
    response = execute_tool(
        client,
        {"tool_name": "crm.get_lead_context", "workflow_id": workflow_id},
        "idem-conflict-2",
    )
    assert response.status_code == 409


def test_tenants_use_the_same_key_independently() -> None:
    client = make_client()
    workflow_a = create_workflow(client, OPERATOR_A)
    workflow_b = create_workflow(client, OPERATOR_B)

    response_a = execute_tool(client, calendar_body(workflow_a), "shared-key-0001", OPERATOR_A)
    response_b = execute_tool(client, calendar_body(workflow_b), "shared-key-0001", OPERATOR_B)

    assert response_a.status_code == 201, response_a.text
    assert response_b.status_code == 201, response_b.text
    assert response_a.json()["execution_id"] != response_b.json()["execution_id"]
    assert response_b.json()["idempotency_replayed"] is False


def test_blocked_attempt_does_not_consume_the_key() -> None:
    adapters, crm, _, _ = counting_adapters()
    client = make_client(tool_adapters=adapters)
    workflow_id = create_workflow(client)
    assert execute_tool(client, task_body(workflow_id), "idem-blocked-01").status_code == 409

    approve(client, workflow_id)
    response = execute_tool(client, task_body(workflow_id), "idem-blocked-01")

    assert response.status_code == 201
    assert response.json()["idempotency_replayed"] is False
    assert crm.task_calls == 1


@pytest.mark.parametrize(
    "key",
    [
        pytest.param(None, id="missing"),
        pytest.param("short", id="too-short"),
        pytest.param("k" * 129, id="too-long"),
        pytest.param("has space key", id="space"),
        pytest.param("bad/slash/key", id="slash"),
    ],
)
def test_missing_or_invalid_idempotency_key_returns_422(key: str | None) -> None:
    adapters, _, calendar, _ = counting_adapters()
    client = make_client(tool_adapters=adapters)
    workflow_id = create_workflow(client)
    headers = actor(OPERATOR_A)
    if key is not None:
        headers["Idempotency-Key"] = key
    response = client.post(
        "/api/v1/tools/execute", json=calendar_body(workflow_id), headers=headers
    )
    assert response.status_code == 422, response.text
    fields = response.json()["detail"]["fields"]
    assert any(f["loc"] == ["header", "Idempotency-Key"] for f in fields)
    assert calendar.calls == 0


def test_valid_key_characters_are_accepted() -> None:
    client = make_client()
    workflow_id = create_workflow(client)
    response = execute_tool(client, calendar_body(workflow_id), "Demo.key_01:A-b")
    assert response.status_code == 201


# --- Fingerprint and repository --------------------------------------------------


def _invocation(duration: int = 60) -> CalendarFindAvailabilityInvocation:
    return CalendarFindAvailabilityInvocation(
        tool_name=ToolName.CALENDAR_FIND_AVAILABILITY,
        workflow_id=UUID("00000000-0000-0000-0000-000000000001"),
        requested_date="2030-03-04",
        duration_minutes=duration,
    )


def _result() -> ToolExecutionResult:
    return ToolExecutionResult(
        execution_id=uuid4(),
        workflow_id=uuid4(),
        tool_name=ToolName.CRM_GET_LEAD_CONTEXT,
        status=ToolExecutionStatus.SUCCEEDED,
        attempts=1,
        idempotency_replayed=False,
        result=LeadContextResult(
            crm_record_id="mock-crm-x",
            lead_id="x",
            lifecycle_stage="new_inquiry",
            intent=LeadIntent.BUY,
            urgency=Urgency.LOW,
            recommended_property_ids=[],
        ),
    )


def test_fingerprint_is_deterministic_and_scoped() -> None:
    assert fingerprint_invocation("tenant-a", _invocation()) == fingerprint_invocation(
        "tenant-a", _invocation()
    )
    assert fingerprint_invocation("tenant-a", _invocation()) != fingerprint_invocation(
        "tenant-b", _invocation()
    )
    assert fingerprint_invocation("tenant-a", _invocation(60)) != fingerprint_invocation(
        "tenant-a", _invocation(90)
    )


def test_key_hash_is_tenant_salted_and_hides_raw_key() -> None:
    hashed = hash_idempotency_key("tenant-a", "demo-key-123")
    assert "demo-key-123" not in hashed
    assert hashed != hash_idempotency_key("tenant-b", "demo-key-123")


def test_repository_decisions() -> None:
    repo = InMemoryIdempotencyRepository()
    assert repo.begin("t", "k", "fp-1").decision is IdempotencyDecision.NEW
    assert repo.begin("t", "k", "fp-1").decision is IdempotencyDecision.IN_PROGRESS
    assert repo.begin("t", "k", "fp-2").decision is IdempotencyDecision.CONFLICT

    result = _result()
    repo.complete("t", "k", "fp-1", result)
    replay = repo.begin("t", "k", "fp-1")
    assert replay.decision is IdempotencyDecision.REPLAY
    assert replay.stored_result == result

    repo.release("t", "k", "fp-1")  # completed records are never released
    assert repo.begin("t", "k", "fp-1").decision is IdempotencyDecision.REPLAY


def test_release_frees_an_in_progress_reservation() -> None:
    repo = InMemoryIdempotencyRepository()
    repo.begin("t", "k", "fp-1")
    repo.release("t", "k", "fp-1")
    assert repo.begin("t", "k", "fp-2").decision is IdempotencyDecision.NEW


def test_concurrent_begin_admits_exactly_one_request() -> None:
    repo = InMemoryIdempotencyRepository()
    barrier = threading.Barrier(16)
    decisions: list[IdempotencyDecision] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        decision = repo.begin("tenant-a", "race-key-001", "fp").decision
        with lock:
            decisions.append(decision)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert decisions.count(IdempotencyDecision.NEW) == 1
    assert decisions.count(IdempotencyDecision.IN_PROGRESS) == 15
