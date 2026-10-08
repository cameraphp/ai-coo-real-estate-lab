from __future__ import annotations

import threading
from typing import Protocol
from uuid import UUID

from app.domain.models import AuditEvent


class AuditRepository(Protocol):
    """Append-only: no update or delete operation exists by design."""

    def append(self, event: AuditEvent) -> None: ...
    def list_for_workflow(self, tenant_id: str, workflow_id: UUID) -> list[AuditEvent]: ...


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def list_for_workflow(self, tenant_id: str, workflow_id: UUID) -> list[AuditEvent]:
        """Events in append (chronological) order. Events are frozen models."""
        with self._lock:
            return [
                e for e in self._events if e.tenant_id == tenant_id and e.workflow_id == workflow_id
            ]
