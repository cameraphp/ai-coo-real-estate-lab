from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.dependencies import correlation_id as resolve_correlation_id
from app.api.dependencies import request_logger as build_request_logger
from app.domain.models import (
    ApprovalRequest,
    ErrorResponse,
    HealthResponse,
    InboundLead,
    WebhookResponse,
    Workflow,
)
from app.observability.logging import SafeLoggerAdapter
from app.services.lead_normalizer import LeadNormalizationError
from app.services.workflow_service import (
    ProposalValidationError,
    WorkflowAlreadyDecidedError,
    WorkflowNotFoundError,
    WorkflowService,
    summarize_workflow,
)

router = APIRouter()


def _get_workflow_service(request: Request) -> WorkflowService:
    return request.app.state.workflow_service  # type: ignore[no-any-return]


def _get_agent_provider(request: Request) -> str:
    return request.app.state.agent_provider  # type: ignore[no-any-return]


AgentProvider = Annotated[str, Depends(_get_agent_provider)]
CorrelationIdDep = Annotated[str, Depends(resolve_correlation_id)]
LoggerDep = Annotated[SafeLoggerAdapter, Depends(build_request_logger)]
WorkflowServiceDep = Annotated[WorkflowService, Depends(_get_workflow_service)]


@router.get("/health", response_model=HealthResponse, tags=["status"])
def health(agent_provider: AgentProvider) -> HealthResponse:
    return HealthResponse(agent_provider=agent_provider)


@router.post(
    "/api/v1/leads/webhook",
    response_model=WebhookResponse,
    responses={
        422: {"model": dict[str, Any]},
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    tags=["leads"],
)
def lead_webhook(
    payload: InboundLead,
    response: Response,
    correlation_id: CorrelationIdDep,
    logger: LoggerDep,
    workflow_service: WorkflowServiceDep,
) -> WebhookResponse:
    logger = logger.with_context(correlation_id=correlation_id)
    try:
        workflow = workflow_service.ingest_lead(payload, correlation_id, logger)
    except LeadNormalizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "correlation_id": correlation_id,
                "error": "lead_validation_failed",
                "detail": str(exc),
            },
        ) from exc
    except ProposalValidationError as exc:
        logger.exception("workflow_error", status="proposal_invalid")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "correlation_id": correlation_id,
                "error": "proposal_validation_failed",
                "detail": str(exc),
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("workflow_error", status="ingest_failure", error_type=type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "correlation_id": correlation_id,
                "error": "ingestion_failed",
                "detail": type(exc).__name__,
            },
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
    responses={
        404: {"model": ErrorResponse},
    },
    tags=["workflows"],
)
def get_workflow(
    workflow_id: UUID,
    correlation_id: CorrelationIdDep,
    logger: LoggerDep,
    workflow_service: WorkflowServiceDep,
) -> Workflow:
    logger = logger.with_context(correlation_id=correlation_id, workflow_id=str(workflow_id))
    try:
        return workflow_service.get_workflow(workflow_id)
    except WorkflowNotFoundError as exc:
        logger.warning("workflow_error", status="not_found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "correlation_id": correlation_id,
                "error": "workflow_not_found",
                "detail": str(exc),
            },
        ) from exc


@router.post(
    "/api/v1/workflows/{workflow_id}/approval",
    response_model=Workflow,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
    tags=["workflows"],
)
def decide_workflow(
    workflow_id: UUID,
    request: ApprovalRequest,
    response: Response,
    correlation_id: CorrelationIdDep,
    logger: LoggerDep,
    workflow_service: WorkflowServiceDep,
) -> Workflow:
    logger = logger.with_context(correlation_id=correlation_id, workflow_id=str(workflow_id))
    try:
        updated = workflow_service.decide(workflow_id, request, logger)
    except WorkflowNotFoundError as exc:
        logger.warning("workflow_error", status="not_found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "correlation_id": correlation_id,
                "error": "workflow_not_found",
                "detail": str(exc),
            },
        ) from exc
    except WorkflowAlreadyDecidedError as exc:
        logger.warning("workflow_error", status="conflict_already_decided")
        response.headers["X-Correlation-ID"] = correlation_id
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "correlation_id": correlation_id,
                "error": "workflow_already_decided",
                "detail": str(exc),
            },
        ) from exc
    response.headers["X-Correlation-ID"] = correlation_id
    return updated
