"""Shared helpers for Week 2 API tests. All data is synthetic."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any, Literal

from fastapi.testclient import TestClient
from httpx import Response

from app.adapters.calendar_mock import MockCalendarAdapter
from app.adapters.crm_mock import MockCrmAdapter
from app.adapters.messaging_mock import MockMessagingAdapter
from app.adapters.tools import PreparedToolCall, ToolAdapters, ToolContext
from app.domain.models import (
    AvailabilityResult,
    FollowUpTaskResult,
    LeadContextResult,
    MessageDraftResult,
)
from app.main import AppSettings, build_app
from app.observability.logging import JsonFormatter, get_logger

OPERATOR_A = "tenant-a-operator"
APPROVER_A = "tenant-a-approver"
VIEWER_A = "tenant-a-viewer"
OPERATOR_B = "tenant-b-operator"

DEMO_LEAD: dict[str, Any] = {
    "lead_id": "lead-week2-001",
    "first_name": "Avery",
    "last_name": "Stone",
    "email": "avery.stone@example.com",
    "phone": "+1 (555) 010-0101",
    "preferred_city": "Austin",
    "budget_max": 650000,
    "min_bedrooms": 3,
    "message": "Looking for a family home near parks. I can tour this weekend.",
}

SENSITIVE_VALUES = (
    "avery.stone@example.com",
    "+1 (555) 010-0101",
    "+15550100101",
    "Looking for a family home near parks",
    "I can tour this weekend",
)


def actor(actor_id: str, **extra: str) -> dict[str, str]:
    return {"X-Demo-Actor-ID": actor_id, **extra}


class CountingCrmAdapter(MockCrmAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.context_calls = 0
        self.task_calls = 0

    def get_lead_context(self, context: ToolContext) -> PreparedToolCall[LeadContextResult]:
        self.context_calls += 1
        return super().get_lead_context(context)

    def prepare_follow_up_task(
        self, context: ToolContext, due_in_hours: int
    ) -> PreparedToolCall[FollowUpTaskResult]:
        self.task_calls += 1
        return super().prepare_follow_up_task(context, due_in_hours)


class CountingCalendarAdapter(MockCalendarAdapter):
    def __init__(self) -> None:
        self.calls = 0

    def find_availability(
        self, context: ToolContext, requested_date: date, duration_minutes: int
    ) -> PreparedToolCall[AvailabilityResult]:
        self.calls += 1
        return super().find_availability(context, requested_date, duration_minutes)


class CountingMessagingAdapter(MockMessagingAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def prepare_follow_up_draft(
        self, context: ToolContext, channel: Literal["email"]
    ) -> PreparedToolCall[MessageDraftResult]:
        self.calls += 1
        return super().prepare_follow_up_draft(context, channel)


def counting_adapters() -> (
    tuple[ToolAdapters, CountingCrmAdapter, CountingCalendarAdapter, CountingMessagingAdapter]
):
    crm = CountingCrmAdapter()
    calendar = CountingCalendarAdapter()
    messaging = CountingMessagingAdapter()
    return ToolAdapters(crm=crm, calendar=calendar, messaging=messaging), crm, calendar, messaging


def make_client(
    tool_adapters: ToolAdapters | None = None,
    settings: AppSettings | None = None,
    raise_server_exceptions: bool = True,
) -> TestClient:
    return TestClient(
        build_app(settings=settings, tool_adapters=tool_adapters),
        raise_server_exceptions=raise_server_exceptions,
    )


def create_workflow(client: TestClient, actor_id: str = OPERATOR_A, **headers: str) -> str:
    response = client.post(
        "/api/v1/leads/webhook", json=DEMO_LEAD, headers=actor(actor_id, **headers)
    )
    assert response.status_code == 200, response.text
    return str(response.json()["workflow"]["workflow_id"])


def approve(client: TestClient, workflow_id: str) -> Response:
    response = client.post(
        f"/api/v1/workflows/{workflow_id}/approval",
        json={"decision": "approve", "comment": "Approved in local demo"},
        headers=actor(APPROVER_A),
    )
    assert response.status_code == 200, response.text
    return response


def execute_tool(
    client: TestClient,
    body: dict[str, Any],
    key: str,
    actor_id: str = OPERATOR_A,
    **headers: str,
) -> Response:
    return client.post(
        "/api/v1/tools/execute",
        json=body,
        headers=actor(actor_id, **{"Idempotency-Key": key, **headers}),
    )


def calendar_body(workflow_id: str, requested_date: str = "2030-03-04") -> dict[str, Any]:
    return {
        "tool_name": "calendar.find_availability",
        "workflow_id": workflow_id,
        "requested_date": requested_date,
        "duration_minutes": 60,
    }


def draft_body(workflow_id: str) -> dict[str, Any]:
    return {
        "tool_name": "messaging.create_follow_up_draft",
        "workflow_id": workflow_id,
        "channel": "email",
    }


def task_body(workflow_id: str, due_in_hours: int = 24) -> dict[str, Any]:
    return {
        "tool_name": "crm.create_follow_up_task",
        "workflow_id": workflow_id,
        "due_in_hours": due_in_hours,
    }


def context_body(workflow_id: str) -> dict[str, Any]:
    return {"tool_name": "crm.get_lead_context", "workflow_id": workflow_id}


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.setFormatter(JsonFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@contextmanager
def captured_app_logs() -> Iterator[list[str]]:
    """Capture formatted JSON log lines emitted by the application logger."""
    handler = _ListHandler()
    logger = get_logger()
    logger.addHandler(handler)
    try:
        yield handler.lines
    finally:
        logger.removeHandler(handler)
