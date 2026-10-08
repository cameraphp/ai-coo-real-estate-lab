"""Explicit mock-tool adapter contracts and typed adapter errors.

Adapters split work into ``prepare`` (compute a result, no state change) and an
optional ``commit`` (store a synthetic in-memory record). The executor only
commits after the attempt finished inside its timeout budget, so a timed-out
call never leaves a mock record behind.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Generic, Literal, Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.models import (
    AvailabilityResult,
    FollowUpTaskResult,
    LeadContextResult,
    LeadIntent,
    MessageDraftResult,
    Urgency,
    WorkflowState,
)

ResultT = TypeVar("ResultT")


class ToolAdapterError(Exception):
    """Base class for classified adapter failures."""


class TransientToolError(ToolAdapterError):
    """A failure that may succeed if retried (bounded by the execution policy)."""


class PermanentToolError(ToolAdapterError):
    """A failure that will not succeed on retry."""


class ToolTimeoutError(ToolAdapterError):
    """The adapter call exceeded its time budget."""


class ToolContext(BaseModel):
    """Safe workflow-derived context handed to adapters. Contains no contact details."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    workflow_id: UUID
    execution_id: UUID
    lead_id: str
    workflow_state: WorkflowState
    intent: LeadIntent
    urgency: Urgency
    recommended_property_ids: tuple[str, ...]
    message_draft: str


@dataclass(frozen=True)
class PreparedToolCall(Generic[ResultT]):
    result: ResultT
    commit: Callable[[], None] | None = None


class CrmAdapter(Protocol):
    def get_lead_context(self, context: ToolContext) -> PreparedToolCall[LeadContextResult]: ...

    def prepare_follow_up_task(
        self, context: ToolContext, due_in_hours: int
    ) -> PreparedToolCall[FollowUpTaskResult]: ...


class CalendarAdapter(Protocol):
    def find_availability(
        self, context: ToolContext, requested_date: date, duration_minutes: int
    ) -> PreparedToolCall[AvailabilityResult]: ...


class MessagingAdapter(Protocol):
    def prepare_follow_up_draft(
        self, context: ToolContext, channel: Literal["email"]
    ) -> PreparedToolCall[MessageDraftResult]: ...


@dataclass(frozen=True)
class ToolAdapters:
    crm: CrmAdapter
    calendar: CalendarAdapter
    messaging: MessagingAdapter
