"""Deterministic local CRM adapter holding synthetic in-memory task records only."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.adapters.tools import PreparedToolCall, ToolContext
from app.domain.models import FollowUpTaskResult, LeadContextResult, WorkflowState

_LIFECYCLE_BY_STATE = {
    WorkflowState.PENDING_APPROVAL: "new_inquiry",
    WorkflowState.APPROVED: "approved_for_follow_up",
    WorkflowState.REJECTED: "closed_rejected",
}


class MockCrmTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    tenant_id: str
    workflow_id: UUID
    lead_id: str
    title: str
    due_at: datetime
    created_at: datetime
    simulated: Literal[True] = True


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MockCrmAdapter:
    def __init__(self, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._tasks: list[MockCrmTask] = []

    def get_lead_context(self, context: ToolContext) -> PreparedToolCall[LeadContextResult]:
        result = LeadContextResult(
            crm_record_id=f"mock-crm-{context.lead_id}",
            lead_id=context.lead_id,
            lifecycle_stage=_LIFECYCLE_BY_STATE[context.workflow_state],
            intent=context.intent,
            urgency=context.urgency,
            recommended_property_ids=list(context.recommended_property_ids),
        )
        return PreparedToolCall(result=result)

    def prepare_follow_up_task(
        self, context: ToolContext, due_in_hours: int
    ) -> PreparedToolCall[FollowUpTaskResult]:
        created_at = self._clock()
        task = MockCrmTask(
            task_id=f"mock-task-{context.execution_id.hex[:12]}",
            tenant_id=context.tenant_id,
            workflow_id=context.workflow_id,
            lead_id=context.lead_id,
            title=f"Follow up with synthetic lead {context.lead_id}",
            due_at=created_at + timedelta(hours=due_in_hours),
            created_at=created_at,
        )
        result = FollowUpTaskResult(task_id=task.task_id, title=task.title, due_at=task.due_at)
        return PreparedToolCall(result=result, commit=lambda: self._store(task))

    def _store(self, task: MockCrmTask) -> None:
        with self._lock:
            self._tasks.append(task)

    def list_tasks(self, tenant_id: str, workflow_id: UUID) -> list[MockCrmTask]:
        with self._lock:
            return [
                t for t in self._tasks if t.tenant_id == tenant_id and t.workflow_id == workflow_id
            ]
