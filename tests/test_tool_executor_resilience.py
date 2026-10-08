from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.adapters.calendar_mock import MockCalendarAdapter
from app.adapters.crm_mock import MockCrmAdapter
from app.adapters.messaging_mock import MockMessagingAdapter
from app.adapters.tools import (
    PermanentToolError,
    PreparedToolCall,
    ToolAdapters,
    ToolContext,
    ToolTimeoutError,
    TransientToolError,
)
from app.domain.models import (
    AvailabilityResult,
    FollowUpTaskResult,
    LeadIntent,
    Urgency,
    WorkflowState,
)
from app.main import AppSettings
from app.services.tool_executor import (
    BoundedToolExecutor,
    ToolExecutionFailure,
    ToolExecutionPolicy,
)
from tests.support import (
    OPERATOR_A,
    actor,
    approve,
    calendar_body,
    create_workflow,
    execute_tool,
    make_client,
    task_body,
)


class StepClock:
    """Monotonic fake clock that advances ``step`` seconds per reading."""

    def __init__(self, step: float) -> None:
        self._now = 0.0
        self._step = step

    def __call__(self) -> float:
        value = self._now
        self._now += self._step
        return value


class ScriptedPrepare:
    """Raises the scripted errors in order, then returns a prepared result."""

    def __init__(self, *errors: Exception) -> None:
        self._errors = list(errors)
        self.calls = 0
        self.committed = 0

    def __call__(self) -> PreparedToolCall[str]:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return PreparedToolCall(result="ok", commit=self._commit)

    def _commit(self) -> None:
        self.committed += 1


def _executor(max_attempts: int = 2, clock: StepClock | None = None) -> BoundedToolExecutor:
    policy = ToolExecutionPolicy(max_attempts=max_attempts, timeout_seconds=0.25)
    return BoundedToolExecutor(policy, clock=clock or StepClock(0.0))


def _context() -> ToolContext:
    return ToolContext(
        tenant_id="tenant-a",
        workflow_id=uuid4(),
        execution_id=uuid4(),
        lead_id="lead-x",
        workflow_state=WorkflowState.APPROVED,
        intent=LeadIntent.BUY,
        urgency=Urgency.LOW,
        recommended_property_ids=(),
        message_draft="draft",
    )


# --- Executor policy ------------------------------------------------------------


def test_transient_error_retries_exactly_once_then_succeeds() -> None:
    prepare = ScriptedPrepare(TransientToolError("blip"))
    attempts: list[int] = []
    success = _executor().run(prepare, attempts.append)
    assert success.result == "ok"
    assert success.attempts == 2
    assert prepare.calls == 2
    assert attempts == [1, 2]
    assert prepare.committed == 1


def test_transient_error_stops_at_max_attempts() -> None:
    prepare = ScriptedPrepare(*(TransientToolError("blip") for _ in range(5)))
    with pytest.raises(ToolExecutionFailure) as info:
        _executor(max_attempts=2).run(prepare, lambda _: None)
    assert info.value.category.value == "transient"
    assert info.value.attempts == 2
    assert prepare.calls == 2
    assert prepare.committed == 0


def test_single_attempt_policy_never_retries() -> None:
    prepare = ScriptedPrepare(TransientToolError("blip"))
    with pytest.raises(ToolExecutionFailure):
        _executor(max_attempts=1).run(prepare, lambda _: None)
    assert prepare.calls == 1


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (PermanentToolError("bad request upstream"), "permanent"),
        (ToolTimeoutError("upstream timeout"), "timeout"),
    ],
)
def test_non_transient_errors_are_not_retried(error: Exception, category: str) -> None:
    prepare = ScriptedPrepare(error)
    with pytest.raises(ToolExecutionFailure) as info:
        _executor(max_attempts=3).run(prepare, lambda _: None)
    assert info.value.category.value == category
    assert info.value.attempts == 1
    assert prepare.calls == 1
    assert prepare.committed == 0


def test_adapter_output_validation_error_is_classified_and_not_retried() -> None:
    def prepare() -> PreparedToolCall[str]:
        AvailabilityResult.model_validate({"kind": "availability"})
        raise AssertionError("unreachable")

    with pytest.raises(ToolExecutionFailure) as info:
        _executor(max_attempts=3).run(prepare, lambda _: None)
    assert info.value.category.value == "validation"
    assert info.value.attempts == 1


def test_unexpected_error_propagates_without_retry() -> None:
    prepare = ScriptedPrepare(RuntimeError("bug"))
    with pytest.raises(RuntimeError):
        _executor(max_attempts=3).run(prepare, lambda _: None)
    assert prepare.calls == 1


def test_slow_prepare_times_out_and_commits_no_mock_record() -> None:
    crm = MockCrmAdapter()
    context = _context()
    executor = _executor(max_attempts=2, clock=StepClock(1.0))

    with pytest.raises(ToolExecutionFailure) as info:
        executor.run(lambda: crm.prepare_follow_up_task(context, 24), lambda _: None)

    assert info.value.category.value == "timeout"
    assert info.value.attempts == 1
    assert crm.list_tasks(context.tenant_id, context.workflow_id) == []


def test_prepare_within_budget_commits_mock_record() -> None:
    crm = MockCrmAdapter()
    context = _context()
    executor = _executor(clock=StepClock(0.1))
    executor.run(lambda: crm.prepare_follow_up_task(context, 24), lambda _: None)
    assert len(crm.list_tasks(context.tenant_id, context.workflow_id)) == 1


@pytest.mark.parametrize(("attempts", "timeout"), [(0, 0.25), (6, 0.25), (2, 0.0), (2, 5.1)])
def test_policy_rejects_unbounded_configuration(attempts: int, timeout: float) -> None:
    with pytest.raises(ValueError):
        ToolExecutionPolicy(max_attempts=attempts, timeout_seconds=timeout)


def test_settings_reject_unbounded_configuration() -> None:
    with pytest.raises(ValidationError):
        AppSettings(tool_max_attempts=50)
    with pytest.raises(ValidationError):
        AppSettings(tool_timeout_seconds=60)
    defaults = AppSettings()
    assert (defaults.tool_max_attempts, defaults.tool_timeout_seconds) == (2, 0.25)


# --- HTTP failure paths ----------------------------------------------------------


class FailingCalendar(MockCalendarAdapter):
    def __init__(self, *errors: Exception) -> None:
        self._errors = list(errors)
        self.calls = 0

    def find_availability(
        self, context: ToolContext, requested_date: date, duration_minutes: int
    ) -> PreparedToolCall[AvailabilityResult]:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return super().find_availability(context, requested_date, duration_minutes)


class TimingOutCrm(MockCrmAdapter):
    def prepare_follow_up_task(
        self, context: ToolContext, due_in_hours: int
    ) -> PreparedToolCall[FollowUpTaskResult]:
        raise ToolTimeoutError("simulated upstream timeout")


def _adapters(
    calendar: MockCalendarAdapter | None = None, crm: MockCrmAdapter | None = None
) -> ToolAdapters:
    return ToolAdapters(
        crm=crm or MockCrmAdapter(),
        calendar=calendar or MockCalendarAdapter(),
        messaging=MockMessagingAdapter(),
    )


def _events(client, workflow_id: str) -> list[dict]:
    return client.get(f"/api/v1/workflows/{workflow_id}/audit", headers=actor(OPERATOR_A)).json()[
        "events"
    ]


def test_http_transient_failure_is_retried_once_and_succeeds() -> None:
    calendar = FailingCalendar(TransientToolError("blip"))
    client = make_client(tool_adapters=_adapters(calendar=calendar))
    workflow_id = create_workflow(client)

    response = execute_tool(client, calendar_body(workflow_id), "retry-key-0001")

    assert response.status_code == 201, response.text
    assert response.json()["attempts"] == 2
    assert calendar.calls == 2
    started = [
        e for e in _events(client, workflow_id) if e["event_type"] == "tool_execution_started"
    ]
    assert [e["attempt"] for e in started] == [1, 2]


def test_http_exhausted_transient_failure_returns_503() -> None:
    calendar = FailingCalendar(*(TransientToolError("blip") for _ in range(4)))
    client = make_client(tool_adapters=_adapters(calendar=calendar))
    workflow_id = create_workflow(client)

    response = execute_tool(client, calendar_body(workflow_id), "retry-key-0002")

    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "tool_execution_failed"
    assert detail["error_category"] == "transient"
    assert detail["attempts"] == 2
    assert calendar.calls == 2
    failed = [e for e in _events(client, workflow_id) if e["event_type"] == "tool_execution_failed"]
    assert failed[0]["error_category"] == "transient"
    assert failed[0]["attempt"] == 2


def test_http_permanent_failure_returns_502_without_retry() -> None:
    calendar = FailingCalendar(PermanentToolError("rejected"))
    client = make_client(tool_adapters=_adapters(calendar=calendar))
    workflow_id = create_workflow(client)

    response = execute_tool(client, calendar_body(workflow_id), "permanent-key-01")

    assert response.status_code == 502, response.text
    assert response.json()["detail"]["error_category"] == "permanent"
    assert response.json()["detail"]["attempts"] == 1
    assert calendar.calls == 1
    assert "rejected" not in response.text


def test_http_timeout_returns_504_and_creates_no_task() -> None:
    crm = TimingOutCrm()
    client = make_client(tool_adapters=_adapters(crm=crm))
    workflow_id = create_workflow(client)
    approve(client, workflow_id)

    response = execute_tool(client, task_body(workflow_id), "timeout-key-0001")

    assert response.status_code == 504, response.text
    assert response.json()["detail"]["error_category"] == "timeout"
    assert crm.list_tasks("tenant-a", UUID(workflow_id)) == []
    workflow = client.get(f"/api/v1/workflows/{workflow_id}", headers=actor(OPERATOR_A)).json()
    assert workflow["side_effect_executed"] is False


def test_failed_execution_releases_the_key_for_a_retry() -> None:
    calendar = FailingCalendar(PermanentToolError("x"))
    client = make_client(tool_adapters=_adapters(calendar=calendar))
    workflow_id = create_workflow(client)
    assert execute_tool(client, calendar_body(workflow_id), "release-key-001").status_code == 502
    retry = execute_tool(client, calendar_body(workflow_id), "release-key-001")
    assert retry.status_code == 201
    assert retry.json()["idempotency_replayed"] is False


def test_unexpected_adapter_error_returns_safe_500() -> None:
    calendar = FailingCalendar(RuntimeError("internal detail that must not leak"))
    client = make_client(tool_adapters=_adapters(calendar=calendar), raise_server_exceptions=False)
    workflow_id = create_workflow(client)

    response = execute_tool(
        client, calendar_body(workflow_id), "internal-key-001", **{"X-Correlation-ID": "cid-500"}
    )

    assert response.status_code == 500
    assert response.json() == {"detail": {"correlation_id": "cid-500", "error": "internal_error"}}
    assert "must not leak" not in response.text
    failed = [e for e in _events(client, workflow_id) if e["event_type"] == "tool_execution_failed"]
    assert failed[0]["error_category"] == "internal"
