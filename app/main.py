from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.adapters.mock_llm import MockLeadProposalAgent
from app.api.routes import router as api_router
from app.observability.logging import configure_logging
from app.repositories.properties import SyntheticJsonPropertyRepository
from app.repositories.workflows import InMemoryWorkflowRepository
from app.services.property_retrieval import PropertyRetrievalService
from app.services.workflow_service import WorkflowService


class AppSettings(BaseSettings):
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


def build_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or AppSettings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="ai-coo-real-estate-lab",
        version="0.1.0",
        description=(
            "Week 1 MVP: Controlled local demo of a real-estate operations assistant "
            "with synthetic data, explicit contracts, and a human approval gate."
        ),
    )

    property_repo = SyntheticJsonPropertyRepository(settings.properties_catalog_path)
    workflow_repo = InMemoryWorkflowRepository()
    retrieval = PropertyRetrievalService(property_repo)

    if settings.agent_provider == "mock":
        agent = MockLeadProposalAgent()
    else:
        agent = MockLeadProposalAgent()

    workflow_service = WorkflowService(
        retrieval_service=retrieval,
        agent=agent,
        workflow_repo=workflow_repo,
    )

    app.state.workflow_service = workflow_service
    app.state.agent_provider = agent.provider_name
    app.state.settings = settings

    app.include_router(api_router)

    return app


app = build_app()
