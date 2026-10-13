from __future__ import annotations

import threading
from typing import Protocol

from app.domain.models import RagAuditEvent


class RagAuditRepository(Protocol):
    """Append-only: no update or delete operation exists by design."""

    def append(self, event: RagAuditEvent) -> None: ...
    def list_for_tenant(self, tenant_id: str) -> list[RagAuditEvent]: ...


class InMemoryRagAuditRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[RagAuditEvent] = []

    def append(self, event: RagAuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def list_for_tenant(self, tenant_id: str) -> list[RagAuditEvent]:
        """Events in append (chronological) order. Events are frozen models."""
        with self._lock:
            return [e for e in self._events if e.tenant_id == tenant_id]
