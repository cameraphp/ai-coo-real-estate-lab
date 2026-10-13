from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.dependencies import (
    IDEMPOTENCY_KEY_MAX_LENGTH,
    IDEMPOTENCY_KEY_MIN_LENGTH,
    IDEMPOTENCY_KEY_PATTERN,
    api_error,
    demo_principal,
    resolve_correlation_id,
)
from app.api.dependencies import correlation_id as resolve_correlation_id_dep
from app.api.dependencies import request_logger as build_request_logger
from app.domain.models import (
    ApprovalRequest,
    DemoPrincipal,
    ErrorCategory,
    ErrorEnvelope,
    ErrorResponse,
    HealthResponse,
    InboundLead,
    RagIndexStatus,
    RagQueryRequest,
    RagQueryResponse,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolInvocationRequest,
    ValidationIssue,
    WebhookResponse,
    Workflow,
    WorkflowAuditTrail,
)
from app.observability.logging import SafeLoggerAdapter
from app.rag.query_service import RagQueryService, RagTopKExceededError
from app.security.tool_policy import ApprovalRequiredError, AuthorizationError
from app.services.lead_normalizer import LeadNormalizationError
from app.services.tool_execution_service import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    ToolExecutionFailedError,
    ToolExecutionService,
)
from app.services.workflow_service import (
    ProposalValidationError,
    WorkflowAlreadyDecidedError,
    WorkflowNotFoundError,
    WorkflowService,
    summarize_workflow,
)

router = APIRouter()

_FAILURE_STATUS: dict[ErrorCategory, int] = {
    ErrorCategory.TRANSIENT: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCategory.TIMEOUT: status.HTTP_504_GATEWAY_TIMEOUT,
}


def _get_workflow_service(request: Request) -> WorkflowService:
    service: WorkflowService = request.app.state.workflow_service
    return service


def _get_tool_execution_service(request: Request) -> ToolExecutionService:
    service: ToolExecutionService = request.app.state.tool_execution_service
    return service


def _get_rag_query_service(request: Request) -> RagQueryService:
    service: RagQueryService = request.app.state.rag_query_service
    return service


def _get_agent_provider(request: Request) -> str:
    provider: str = request.app.state.agent_provider
    return provider


AgentProvider = Annotated[str, Depends(_get_agent_provider)]
CorrelationIdDep = Annotated[str, Depends(resolve_correlation_id_dep)]
LoggerDep = Annotated[SafeLoggerAdapter, Depends(build_request_logger)]
PrincipalDep = Annotated[DemoPrincipal, Depends(demo_principal)]
WorkflowServiceDep = Annotated[WorkflowService, Depends(_get_workflow_service)]
ToolServiceDep = Annotated[ToolExecutionService, Depends(_get_tool_execution_service)]
RagServiceDep = Annotated[RagQueryService, Depends(_get_rag_query_service)]
IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=IDEMPOTENCY_KEY_MIN_LENGTH,
        max_length=IDEMPOTENCY_KEY_MAX_LENGTH,
        pattern=IDEMPOTENCY_KEY_PATTERN,
    ),
]

_AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}


def _not_found(correlation_id: str, exc: Exception) -> Exception:
    return api_error(status.HTTP_404_NOT_FOUND, correlation_id, "workflow_not_found", str(exc))


def _forbidden(correlation_id: str, exc: Exception) -> Exception:
    return api_error(
        status.HTTP_403_FORBIDDEN,
        correlation_id,
        "forbidden",
        str(exc),
        error_category=ErrorCategory.AUTHORIZATION,
    )


@router.get("/health", response_model=HealthResponse, tags=["status"])
def health(agent_provider: AgentProvider) -> HealthResponse:
    return HealthResponse(agent_provider=agent_provider)


@router.post(
    "/api/v1/leads/webhook",
    response_model=WebhookResponse,
    responses={
        **_AUTH_RESPONSES,
        400: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
    },
    tags=["leads"],
)
def lead_webhook(
    payload: InboundLead,
    response: Response,
    principal: PrincipalDep,
    correlation_id: CorrelationIdDep,
    logger: LoggerDep,
    workflow_service: WorkflowServiceDep,
) -> WebhookResponse:
    logger = logger.with_context(correlation_id=correlation_id)
    try:
        workflow = workflow_service.ingest_lead(principal, payload, correlation_id, logger)
    except AuthorizationError as exc:
        raise _forbidden(correlation_id, exc) from exc
    except LeadNormalizationError as exc:
        raise api_error(
            status.HTTP_400_BAD_REQUEST, correlation_id, "lead_validation_failed", str(exc)
        ) from exc
    except ProposalValidationError as exc:
        logger.exception("workflow_error", status="proposal_invalid")
        raise api_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            correlation_id,
            "proposal_validation_failed",
            str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("workflow_error", status="ingest_failure", error_type=type(exc).__name__)
        raise api_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            correlation_id,
            "ingestion_failed",
            type(exc).__name__,
        ) from exc

    response.headers["X-Correlation-ID"] = correlation_id
    return WebhookResponse(
        correlation_id=correlation_id,
        workflow=summarize_workflow(workflow),
        retrieved_count=len(workflow.retrieved_properties),
        side_effect_executed=False,
        message=(
            "Workflow created and awaiting human approval. "
            "No external action or side effect has occurred."
        ),
    )


@router.get(
    "/api/v1/workflows/{workflow_id}",
    response_model=Workflow,
    responses=_AUTH_RESPONSES,
    tags=["workflows"],
)
def get_workflow(
    workflow_id: UUID,
    response: Response,
    principal: PrincipalDep,
    correlation_id: CorrelationIdDep,
    logger: LoggerDep,
    workflow_service: WorkflowServiceDep,
) -> Workflow:
    logger = logger.with_context(correlation_id=correlation_id, workflow_id=str(workflow_id))
    try:
        workflow = workflow_service.get_workflow(principal, workflow_id)
    except WorkflowNotFoundError as exc:
        logger.warning("workflow_error", status="not_found")
        raise _not_found(correlation_id, exc) from exc
    except AuthorizationError as exc:
        raise _forbidden(correlation_id, exc) from exc
    response.headers["X-Correlation-ID"] = correlation_id
    return workflow


@router.post(
    "/api/v1/workflows/{workflow_id}/approval",
    response_model=Workflow,
    responses={**_AUTH_RESPONSES, 409: {"model": ErrorEnvelope}},
    tags=["workflows"],
)
def decide_workflow(
    workflow_id: UUID,
    request: ApprovalRequest,
    response: Response,
    principal: PrincipalDep,
    correlation_id: CorrelationIdDep,
    logger: LoggerDep,
    workflow_service: WorkflowServiceDep,
) -> Workflow:
    logger = logger.with_context(correlation_id=correlation_id, workflow_id=str(workflow_id))
    try:
        updated = workflow_service.decide(principal, workflow_id, request, correlation_id, logger)
    except WorkflowNotFoundError as exc:
        logger.warning("workflow_error", status="not_found")
        raise _not_found(correlation_id, exc) from exc
    except AuthorizationError as exc:
        raise _forbidden(correlation_id, exc) from exc
    except WorkflowAlreadyDecidedError as exc:
        logger.warning("workflow_error", status="conflict_already_decided")
        raise api_error(
            status.HTTP_409_CONFLICT, correlation_id, "workflow_already_decided", str(exc)
        ) from exc
    response.headers["X-Correlation-ID"] = correlation_id
    return updated


@router.get(
    "/api/v1/workflows/{workflow_id}/audit",
    response_model=WorkflowAuditTrail,
    responses=_AUTH_RESPONSES,
    tags=["workflows"],
)
def get_workflow_audit(
    workflow_id: UUID,
    response: Response,
    principal: PrincipalDep,
    correlation_id: CorrelationIdDep,
    workflow_service: WorkflowServiceDep,
) -> WorkflowAuditTrail:
    try:
        events = workflow_service.get_audit_trail(principal, workflow_id)
    except WorkflowNotFoundError as exc:
        raise _not_found(correlation_id, exc) from exc
    except AuthorizationError as exc:
        raise _forbidden(correlation_id, exc) from exc
    response.headers["X-Correlation-ID"] = correlation_id
    return WorkflowAuditTrail(workflow_id=workflow_id, events=events)


@router.post(
    "/api/v1/tools/execute",
    response_model=ToolExecutionResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        **_AUTH_RESPONSES,
        200: {"model": ToolExecutionResult, "description": "Idempotent replay"},
        409: {"model": ErrorEnvelope},
        502: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
        504: {"model": ErrorEnvelope},
    },
    tags=["tools"],
)
def execute_tool(
    body: ToolInvocationRequest,
    response: Response,
    principal: PrincipalDep,
    idempotency_key: IdempotencyKeyHeader,
    correlation_id: CorrelationIdDep,
    logger: LoggerDep,
    tool_service: ToolServiceDep,
) -> ToolExecutionResult:
    try:
        result = tool_service.execute(principal, body.root, idempotency_key, correlation_id, logger)
    except WorkflowNotFoundError as exc:
        raise _not_found(correlation_id, exc) from exc
    except AuthorizationError as exc:
        raise _forbidden(correlation_id, exc) from exc
    except ApprovalRequiredError as exc:
        raise api_error(
            status.HTTP_409_CONFLICT,
            correlation_id,
            "approval_required",
            str(exc),
            error_category=ErrorCategory.APPROVAL_REQUIRED,
        ) from exc
    except IdempotencyConflictError as exc:
        raise api_error(
            status.HTTP_409_CONFLICT,
            correlation_id,
            "idempotency_key_reused",
            str(exc),
            error_category=ErrorCategory.IDEMPOTENCY_CONFLICT,
        ) from exc
    except IdempotencyInProgressError as exc:
        raise api_error(
            status.HTTP_409_CONFLICT,
            correlation_id,
            "idempotency_request_in_progress",
            str(exc),
            error_category=ErrorCategory.IDEMPOTENCY_CONFLICT,
        ) from exc
    except ToolExecutionFailedError as exc:
        raise api_error(
            _FAILURE_STATUS.get(exc.category, status.HTTP_502_BAD_GATEWAY),
            correlation_id,
            "tool_execution_failed",
            str(exc),
            error_category=exc.category,
            execution_id=exc.execution_id,
            attempts=exc.attempts,
        ) from exc

    response.headers["X-Correlation-ID"] = correlation_id
    if result.status is ToolExecutionStatus.REPLAYED:
        response.status_code = status.HTTP_200_OK
    return result


@router.post(
    "/api/v1/rag/query",
    response_model=RagQueryResponse,
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
    tags=["rag"],
)
def rag_query(
    body: RagQueryRequest,
    response: Response,
    principal: PrincipalDep,
    correlation_id: CorrelationIdDep,
    logger: LoggerDep,
    rag_service: RagServiceDep,
) -> RagQueryResponse:
    """Read-only, tenant-scoped RAG query. Insufficient evidence is a normal 200 result."""
    try:
        outcome = rag_service.query(principal, body, correlation_id, logger)
    except AuthorizationError as exc:
        raise _forbidden(correlation_id, exc) from exc
    except RagTopKExceededError as exc:
        raise api_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            correlation_id,
            "request_validation_failed",
            "Request headers or body failed schema validation.",
            error_category=ErrorCategory.VALIDATION,
            fields=[ValidationIssue(loc=["body", "top_k"], msg=str(exc), type="less_than_equal")],
        ) from exc
    response.headers["X-Correlation-ID"] = correlation_id
    return outcome.response


@router.get(
    "/api/v1/rag/index/status",
    response_model=RagIndexStatus,
    responses={401: {"model": ErrorEnvelope}, 403: {"model": ErrorEnvelope}},
    tags=["rag"],
)
def rag_index_status(
    response: Response,
    principal: PrincipalDep,
    correlation_id: CorrelationIdDep,
    rag_service: RagServiceDep,
) -> RagIndexStatus:
    """Safe index summary for the caller's own tenant only."""
    try:
        index_status = rag_service.index_status(principal)
    except AuthorizationError as exc:
        raise _forbidden(correlation_id, exc) from exc
    response.headers["X-Correlation-ID"] = correlation_id
    return index_status


async def _request_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 without echoing rejected input values back to the client."""
    cid = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    issues = [
        ValidationIssue(loc=list(err.get("loc", ())), msg=err.get("msg", ""), type=err["type"])
        for err in exc.errors()
    ]
    body = ErrorResponse(
        correlation_id=cid,
        error="request_validation_failed",
        detail="Request headers or body failed schema validation.",
        error_category=ErrorCategory.VALIDATION,
        fields=issues,
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": body.model_dump(mode="json", exclude_none=True)},
        headers={"X-Correlation-ID": cid},
    )


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Generic 500 without exception text or traceback."""
    cid = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    body = ErrorResponse(correlation_id=cid, error="internal_error", detail=None)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": body.model_dump(mode="json", exclude_none=True)},
        headers={"X-Correlation-ID": cid},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _request_validation_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
