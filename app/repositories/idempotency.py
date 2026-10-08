from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from app.domain.models import ToolExecutionResult


class IdempotencyDecision(str, Enum):
    NEW = "new"
    REPLAY = "replay"
    CONFLICT = "conflict"
    IN_PROGRESS = "in_progress"


@dataclass(frozen=True)
class IdempotencyBeginResult:
    decision: IdempotencyDecision
    stored_result: ToolExecutionResult | None = None


@dataclass
class _IdempotencyRecord:
    fingerprint: str
    result: ToolExecutionResult | None = None


class IdempotencyRepository(Protocol):
    def begin(self, tenant_id: str, key: str, fingerprint: str) -> IdempotencyBeginResult: ...
    def complete(
        self, tenant_id: str, key: str, fingerprint: str, result: ToolExecutionResult
    ) -> None: ...
    def release(self, tenant_id: str, key: str, fingerprint: str) -> None: ...


class InMemoryIdempotencyRepository:
    """Tenant-scoped idempotency records. Every decision runs under one lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[tuple[str, str], _IdempotencyRecord] = {}

    def begin(self, tenant_id: str, key: str, fingerprint: str) -> IdempotencyBeginResult:
        with self._lock:
            record = self._records.get((tenant_id, key))
            if record is None:
                self._records[(tenant_id, key)] = _IdempotencyRecord(fingerprint=fingerprint)
                return IdempotencyBeginResult(IdempotencyDecision.NEW)
            if record.fingerprint != fingerprint:
                return IdempotencyBeginResult(IdempotencyDecision.CONFLICT)
            if record.result is None:
                return IdempotencyBeginResult(IdempotencyDecision.IN_PROGRESS)
            return IdempotencyBeginResult(IdempotencyDecision.REPLAY, record.result)

    def complete(
        self, tenant_id: str, key: str, fingerprint: str, result: ToolExecutionResult
    ) -> None:
        with self._lock:
            record = self._records.get((tenant_id, key))
            if record is not None and record.fingerprint == fingerprint:
                record.result = result

    def release(self, tenant_id: str, key: str, fingerprint: str) -> None:
        """Drop an in-progress reservation so a non-successful attempt can be retried."""
        with self._lock:
            record = self._records.get((tenant_id, key))
            if record is not None and record.fingerprint == fingerprint and record.result is None:
                del self._records[(tenant_id, key)]
