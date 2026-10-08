"""Orchestrates one mock-tool invocation.

Order of checks: tenant-scoped workflow lookup (404) -> role/tool allowlist (403)
-> idempotency decision (replay or 409) -> human-approval gate (409) -> bounded
execution -> idempotency completion. Every decision is written to the audit trail.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, assert_never
from uuid import UUID, uuid4

from app.adapters.tools import PreparedToolCall, ToolAdapters, ToolContext
from app.domain.models import (
    AuditEventType,
    AuditOutcome,
    CalendarFindAvailabilityInvocation,
    CrmCreateFollowUpTaskInvocation,
    CrmGetLeadContextInvocation,
    DemoPrincipal,
    ErrorCategory,
    MessagingCreateFollowUpDraftInvocation,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolInvocation,
    ToolResultPayload,
    Workflow,
)
from app.observability.logging import SafeLoggerAdapter
from app.repositories.idempotency import IdempotencyDecision, IdempotencyRepository
from app.repositories.workflows import WorkflowRepository
from app.security.tool_policy import (
    ApprovalRequiredError,
    AuthorizationError,
    authorize_tool,
    ensure_approval_satisfied,
)
from app.services.audit_service import AuditService, hash_idempotency_key
from app.services.tool_executor import BoundedToolExecutor, ToolExecutionFailure
from app.services.workflow_service import WorkflowNotFoundError


class IdempotencyConflictError(RuntimeError):
    pass


class IdempotencyInProgressError(RuntimeError):
    pass


class ToolExecutionFailedError(RuntimeError):
    def __init__(self, category: ErrorCategory, attempts: int, execution_id: UUID) -> None:
        super().__init__(f"Mock tool execution failed ({category.value}).")
        self.category = category
        self.attempts = attempts
        self.execution_id = execution_id


def fingerprint_invocation(tenant_id: str, invocation: ToolInvocation) -> str:
    """Deterministic hash of the validated, PII-free invocation identity."""
    canonical = json.dumps(
        {"tenant_id": tenant_id, "invocation": invocation.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _tool_context(workflow: Workflow, execution_id: UUID) -> ToolContext:
    return ToolContext(
        tenant_id=workflow.tenant_id,
        workflow_id=workflow.workflow_id,
        execution_id=execution_id,
        lead_id=workflow.lead_id,
        workflow_state=workflow.state,
        intent=workflow.proposal.intent,
        urgency=workflow.proposal.urgency,
        recommended_property_ids=tuple(workflow.proposal.recommended_property_ids),
        message_draft=workflow.proposal.message_draft,
    )


class ToolExecutionService:
    def __init__(
        self,
        workflow_repo: WorkflowRepository,
        idempotency_repo: IdempotencyRepository,
        adapters: ToolAdapters,
        executor: BoundedToolExecutor,
        audit: AuditService,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._workflows = workflow_repo
        self._idempotency = idempotency_repo
        self._adapters = adapters
        self._executor = executor
        self._audit = audit
        self._new_id = id_factory

    def execute(
        self,
        principal: DemoPrincipal,
        invocation: ToolInvocation,
        idempotency_key: str,
        correlation_id: str,
        logger: SafeLoggerAdapter,
    ) -> ToolExecutionResult:
        workflow = self._workflows.get_for_tenant(principal.tenant_id, invocation.workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(f"Workflow {invocation.workflow_id} not found.")

        tool_name = invocation.tool_name
        key_hash = hash_idempotency_key(principal.tenant_id, idempotency_key)
        log = logger.with_context(
            correlation_id=correlation_id,
            tenant_id=principal.tenant_id,
            actor_id=principal.actor_id,
            workflow_id=str(workflow.workflow_id),
            tool_name=tool_name.value,
        )

        def audit(event_type: AuditEventType, outcome: AuditOutcome, **extra: Any) -> None:
            self._audit.record(
                event_type=event_type,
                outcome=outcome,
                principal=principal,
                workflow_id=workflow.workflow_id,
                correlation_id=correlation_id,
                tool_name=tool_name,
                idempotency_key_hash=key_hash,
                **extra,
            )

        try:
            policy = authorize_tool(principal, tool_name)
        except AuthorizationError:
            audit(
                AuditEventType.TOOL_EXECUTION_DENIED,
                AuditOutcome.DENIED,
                error_category=ErrorCategory.AUTHORIZATION,
            )
            log.warning("tool_execution_denied", status="denied", error_category="authorization")
            raise

        fingerprint = fingerprint_invocation(principal.tenant_id, invocation)
        begin = self._idempotency.begin(principal.tenant_id, idempotency_key, fingerprint)
        if begin.decision in (IdempotencyDecision.CONFLICT, IdempotencyDecision.IN_PROGRESS):
            audit(
                AuditEventType.IDEMPOTENCY_CONFLICT,
                AuditOutcome.CONFLICT,
                error_category=ErrorCategory.IDEMPOTENCY_CONFLICT,
            )
            log.warning(
                "idempotency_conflict",
                status=begin.decision.value,
                error_category="idempotency_conflict",
            )
            if begin.decision is IdempotencyDecision.CONFLICT:
                raise IdempotencyConflictError(
                    "Idempotency-Key was already used with a different request."
                )
            raise IdempotencyInProgressError("A request with this Idempotency-Key is in progress.")
        if begin.decision is IdempotencyDecision.REPLAY and begin.stored_result is not None:
            stored = begin.stored_result
            audit(
                AuditEventType.TOOL_EXECUTION_REPLAYED,
                AuditOutcome.REPLAYED,
                execution_id=stored.execution_id,
            )
            log.info("tool_execution_replayed", status="replayed", attempt=stored.attempts)
            return stored.model_copy(
                update={"status": ToolExecutionStatus.REPLAYED, "idempotency_replayed": True}
            )

        try:
            ensure_approval_satisfied(policy, workflow.state)
        except ApprovalRequiredError:
            self._idempotency.release(principal.tenant_id, idempotency_key, fingerprint)
            audit(
                AuditEventType.TOOL_EXECUTION_BLOCKED_APPROVAL,
                AuditOutcome.BLOCKED,
                error_category=ErrorCategory.APPROVAL_REQUIRED,
            )
            log.warning(
                "tool_execution_blocked", status="blocked", error_category="approval_required"
            )
            raise

        execution_id = self._new_id()
        context = _tool_context(workflow, execution_id)

        def on_attempt(attempt: int) -> None:
            audit(
                AuditEventType.TOOL_EXECUTION_STARTED,
                AuditOutcome.STARTED,
                execution_id=execution_id,
                attempt=attempt,
            )
            log.info("tool_execution_started", status="started", attempt=attempt)

        try:
            success = self._executor.run(lambda: self._prepare(invocation, context), on_attempt)
        except ToolExecutionFailure as failure:
            self._idempotency.release(principal.tenant_id, idempotency_key, fingerprint)
            audit(
                AuditEventType.TOOL_EXECUTION_FAILED,
                AuditOutcome.FAILED,
                execution_id=execution_id,
                attempt=failure.attempts,
                error_category=failure.category,
            )
            log.warning(
                "tool_execution_failed",
                status="failed",
                attempt=failure.attempts,
                error_category=failure.category.value,
                error_type=failure.error_type,
            )
            error = ToolExecutionFailedError(failure.category, failure.attempts, execution_id)
            raise error from failure
        except Exception as exc:
            self._idempotency.release(principal.tenant_id, idempotency_key, fingerprint)
            audit(
                AuditEventType.TOOL_EXECUTION_FAILED,
                AuditOutcome.FAILED,
                execution_id=execution_id,
                error_category=ErrorCategory.INTERNAL,
            )
            log.error(
                "tool_execution_failed",
                status="failed",
                error_category="internal",
                error_type=type(exc).__name__,
            )
            raise

        result = ToolExecutionResult(
            execution_id=execution_id,
            workflow_id=workflow.workflow_id,
            tool_name=tool_name,
            status=ToolExecutionStatus.SUCCEEDED,
            attempts=success.attempts,
            idempotency_replayed=False,
            result=success.result,
        )
        self._idempotency.complete(principal.tenant_id, idempotency_key, fingerprint, result)
        audit(
            AuditEventType.TOOL_EXECUTION_SUCCEEDED,
            AuditOutcome.SUCCEEDED,
            execution_id=execution_id,
            attempt=success.attempts,
        )
        log.info("tool_execution_succeeded", status="succeeded", attempt=success.attempts)
        return result

    def _prepare(
        self, invocation: ToolInvocation, context: ToolContext
    ) -> PreparedToolCall[ToolResultPayload]:
        """Closed dispatch over the validated invocation type. No dynamic lookup."""
        match invocation:
            case CrmGetLeadContextInvocation():
                return self._adapters.crm.get_lead_context(context)
            case CalendarFindAvailabilityInvocation():
                return self._adapters.calendar.find_availability(
                    context, invocation.requested_date, invocation.duration_minutes
                )
            case MessagingCreateFollowUpDraftInvocation():
                return self._adapters.messaging.prepare_follow_up_draft(context, invocation.channel)
            case CrmCreateFollowUpTaskInvocation():
                return self._adapters.crm.prepare_follow_up_task(context, invocation.due_in_hours)
        assert_never(invocation)
