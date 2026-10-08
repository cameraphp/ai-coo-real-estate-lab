from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.models import Workflow


class WorkflowRepository(Protocol):
    def save(self, workflow: Workflow) -> None: ...
    def get_for_tenant(self, tenant_id: str, workflow_id: UUID) -> Workflow | None: ...


class InMemoryWorkflowRepository:
    def __init__(self) -> None:
        self._store: dict[UUID, Workflow] = {}

    def save(self, workflow: Workflow) -> None:
        self._store[workflow.workflow_id] = workflow.model_copy(deep=True)

    def get_for_tenant(self, tenant_id: str, workflow_id: UUID) -> Workflow | None:
        """Return the workflow only when it belongs to ``tenant_id``; otherwise ``None``."""
        stored = self._store.get(workflow_id)
        if stored is None or stored.tenant_id != tenant_id:
            return None
        return stored.model_copy(deep=True)
