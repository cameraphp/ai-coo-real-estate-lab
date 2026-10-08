"""Local draft-only messaging adapter. It never sends email or SMS."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.adapters.tools import PreparedToolCall, ToolContext
from app.domain.models import MessageDraftResult


class MockMessageDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: str
    tenant_id: str
    workflow_id: UUID
    lead_id: str
    channel: Literal["email"]
    body: str
    status: Literal["DRAFT"] = "DRAFT"
    sent: Literal[False] = False
    simulated: Literal[True] = True
    created_at: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MockMessagingAdapter:
    def __init__(self, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._drafts: list[MockMessageDraft] = []

    def prepare_follow_up_draft(
        self, context: ToolContext, channel: Literal["email"]
    ) -> PreparedToolCall[MessageDraftResult]:
        draft = MockMessageDraft(
            draft_id=f"mock-draft-{context.execution_id.hex[:12]}",
            tenant_id=context.tenant_id,
            workflow_id=context.workflow_id,
            lead_id=context.lead_id,
            channel=channel,
            body=context.message_draft,
            created_at=self._clock(),
        )
        result = MessageDraftResult(
            draft_id=draft.draft_id,
            channel=draft.channel,
            recipient_lead_id=draft.lead_id,
            body_length=len(draft.body),
        )
        return PreparedToolCall(result=result, commit=lambda: self._store(draft))

    def _store(self, draft: MockMessageDraft) -> None:
        with self._lock:
            self._drafts.append(draft)

    def list_drafts(self, tenant_id: str, workflow_id: UUID) -> list[MockMessageDraft]:
        with self._lock:
            return [
                d for d in self._drafts if d.tenant_id == tenant_id and d.workflow_id == workflow_id
            ]
