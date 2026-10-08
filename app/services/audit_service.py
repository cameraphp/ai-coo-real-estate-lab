from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.models import (
    AuditEvent,
    AuditEventType,
    AuditOutcome,
    DemoPrincipal,
    ErrorCategory,
    ToolName,
)
from app.repositories.audit import AuditRepository


def hash_idempotency_key(tenant_id: str, key: str) -> str:
    """Short, tenant-salted identifier so audit records never hold the raw key."""
    digest = hashlib.sha256(f"{tenant_id}:{key}".encode()).hexdigest()
    return f"sha256:{digest[:16]}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuditService:
    def __init__(
        self,
        repository: AuditRepository,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self._clock = clock

    def record(
        self,
        *,
        event_type: AuditEventType,
        outcome: AuditOutcome,
        principal: DemoPrincipal,
        workflow_id: UUID,
        correlation_id: str,
        tool_name: ToolName | None = None,
        execution_id: UUID | None = None,
        idempotency_key_hash: str | None = None,
        attempt: int | None = None,
        error_category: ErrorCategory | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=uuid4(),
            occurred_at=self._clock(),
            correlation_id=correlation_id,
            tenant_id=principal.tenant_id,
            actor_id=principal.actor_id,
            workflow_id=workflow_id,
            event_type=event_type,
            outcome=outcome,
            tool_name=tool_name,
            execution_id=execution_id,
            idempotency_key_hash=idempotency_key_hash,
            attempt=attempt,
            error_category=error_category,
        )
        self._repository.append(event)
        return event

    def list_for_workflow(self, tenant_id: str, workflow_id: UUID) -> list[AuditEvent]:
        return self._repository.list_for_workflow(tenant_id, workflow_id)
