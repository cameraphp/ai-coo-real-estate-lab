from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from pydantic import Field
from pydantic_settings import SettingsConfigDict

from app.adapters.calendar_mock import MockCalendarAdapter
from app.adapters.crm_mock import MockCrmAdapter
from app.adapters.messaging_mock import MockMessagingAdapter
from app.adapters.mock_llm import MockLeadProposalAgent
from app.adapters.tools import ToolAdapters
from app.api.routes import register_exception_handlers
from app.api.routes import router as api_router
from app.observability.logging import configure_logging
from app.rag.config import RagSettings
from app.rag.query_service import build_rag_query_service
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.idempotency import InMemoryIdempotencyRepository
from app.repositories.properties import SyntheticJsonPropertyRepository
from app.repositories.rag_audit import InMemoryRagAuditRepository
from app.repositories.workflows import InMemoryWorkflowRepository
from app.services.audit_service import AuditService
from app.services.property_retrieval import PropertyRetrievalService
from app.services.tool_execution_service import ToolExecutionService
from app.services.tool_executor import (
    MAX_ATTEMPTS_LIMIT,
    MAX_TIMEOUT_SECONDS,
    BoundedToolExecutor,
    ToolExecutionPolicy,
)
from app.services.workflow_service import WorkflowService


class AppSettings(RagSettings):
    """Application settings. The bounded ``RAG_*`` settings are inherited from RagSettings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    agent_provider: str = "mock"
    properties_catalog_path: Path | None = None
    tool_max_attempts: int = Field(default=2, ge=1, le=MAX_ATTEMPTS_LIMIT)
    tool_timeout_seconds: float = Field(default=0.25, gt=0, le=MAX_TIMEOUT_SECONDS)


def default_tool_adapters() -> ToolAdapters:
    return ToolAdapters(
        crm=MockCrmAdapter(),
        calendar=MockCalendarAdapter(),
        messaging=MockMessagingAdapter(),
    )


def build_app(
    settings: AppSettings | None = None,
    tool_adapters: ToolAdapters | None = None,
) -> FastAPI:
    settings = settings or AppSettings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="ai-coo-real-estate-lab",
        version="0.4.0",
        description=(
            "Week 4: Controlled local demo of a real-estate operations assistant with "
            "synthetic data, a human approval gate, local mock tools, demo tenant/role "
            "boundaries, idempotency, a queryable audit trail, and a deterministic "
            "tenant-scoped RAG lab, packaged for local container runs. No external model, "
            "service, or side effect."
        ),
    )

    property_repo = SyntheticJsonPropertyRepository(settings.properties_catalog_path)
    workflow_repo = InMemoryWorkflowRepository()
    retrieval = PropertyRetrievalService(property_repo)
    audit_service = AuditService(InMemoryAuditRepository())

    if settings.agent_provider == "mock":
        agent = MockLeadProposalAgent()
    else:
        agent = MockLeadProposalAgent()

    workflow_service = WorkflowService(
        retrieval_service=retrieval,
        agent=agent,
        workflow_repo=workflow_repo,
        audit=audit_service,
    )
    tool_execution_service = ToolExecutionService(
        workflow_repo=workflow_repo,
        idempotency_repo=InMemoryIdempotencyRepository(),
        adapters=tool_adapters or default_tool_adapters(),
        executor=BoundedToolExecutor(
            ToolExecutionPolicy(
                max_attempts=settings.tool_max_attempts,
                timeout_seconds=settings.tool_timeout_seconds,
            )
        ),
        audit=audit_service,
    )

    # Ingests the committed synthetic corpus now; an invalid fixture fails startup.
    rag_audit_repository = InMemoryRagAuditRepository()
    rag_query_service = build_rag_query_service(settings.rag_config(), rag_audit_repository)

    app.state.workflow_service = workflow_service
    app.state.tool_execution_service = tool_execution_service
    app.state.rag_query_service = rag_query_service
    app.state.rag_audit_repository = rag_audit_repository
    app.state.agent_provider = agent.provider_name
    app.state.settings = settings

    app.include_router(api_router)
    register_exception_handlers(app)

    return app


app = build_app()
