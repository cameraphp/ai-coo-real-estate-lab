"""Bounded execution policy: limited retries, cooperative timeout, typed classification.

Only ``TransientToolError`` is retried, immediately and without backoff, because
every adapter is an in-memory mock. Timeouts are detected cooperatively: the
elapsed time of ``prepare`` is checked before ``commit``; a running call is not
preempted. A timed-out attempt is discarded without committing a mock record.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import ValidationError

from app.adapters.tools import (
    PermanentToolError,
    PreparedToolCall,
    ToolTimeoutError,
    TransientToolError,
)
from app.domain.models import ErrorCategory

ResultT = TypeVar("ResultT")

MAX_ATTEMPTS_LIMIT = 5
MAX_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ToolExecutionPolicy:
    max_attempts: int = 2
    timeout_seconds: float = 0.25

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= MAX_ATTEMPTS_LIMIT:
            raise ValueError(f"max_attempts must be between 1 and {MAX_ATTEMPTS_LIMIT}.")
        if not 0 < self.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds must be in (0, {MAX_TIMEOUT_SECONDS}].")


@dataclass(frozen=True)
class ExecutionSuccess(Generic[ResultT]):
    result: ResultT
    attempts: int


class ToolExecutionFailure(Exception):
    def __init__(self, category: ErrorCategory, attempts: int, error_type: str) -> None:
        super().__init__(f"Tool execution failed: {category.value} after {attempts} attempt(s).")
        self.category = category
        self.attempts = attempts
        self.error_type = error_type


class BoundedToolExecutor:
    def __init__(
        self,
        policy: ToolExecutionPolicy,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._clock = clock

    @property
    def policy(self) -> ToolExecutionPolicy:
        return self._policy

    def run(
        self,
        prepare: Callable[[], PreparedToolCall[ResultT]],
        on_attempt: Callable[[int], None],
    ) -> ExecutionSuccess[ResultT]:
        for attempt in range(1, self._policy.max_attempts + 1):
            on_attempt(attempt)
            started = self._clock()
            try:
                prepared = prepare()
            except TransientToolError as exc:
                if attempt < self._policy.max_attempts:
                    continue
                raise ToolExecutionFailure(
                    ErrorCategory.TRANSIENT, attempt, type(exc).__name__
                ) from exc
            except ToolTimeoutError as exc:
                raise ToolExecutionFailure(
                    ErrorCategory.TIMEOUT, attempt, type(exc).__name__
                ) from exc
            except PermanentToolError as exc:
                raise ToolExecutionFailure(
                    ErrorCategory.PERMANENT, attempt, type(exc).__name__
                ) from exc
            except ValidationError as exc:
                raise ToolExecutionFailure(
                    ErrorCategory.VALIDATION, attempt, type(exc).__name__
                ) from exc

            if self._clock() - started > self._policy.timeout_seconds:
                raise ToolExecutionFailure(ErrorCategory.TIMEOUT, attempt, "ToolTimeoutError")
            if prepared.commit is not None:
                prepared.commit()
            return ExecutionSuccess(result=prepared.result, attempts=attempt)

        raise AssertionError("unreachable: max_attempts is validated to be >= 1")
