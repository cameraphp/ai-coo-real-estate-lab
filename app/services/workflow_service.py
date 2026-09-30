from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.adapters.llm import LeadProposalAgent, LeadProposalAgentError
from app.domain.models import (
    AgentProposal,
    ApprovalMetadata,
    ApprovalRequest,
    InboundLead,
    NormalizedLead,
    RankedProperty,
    Workflow,
    WorkflowState,
    WorkflowSummary,
)
from app.domain.workflow import can_transition, next_state_for
from app.observability.logging import SafeLoggerAdapter
from app.repositories.workflows import WorkflowRepository
from app.services.lead_normalizer import LeadNormalizationError, normalize_lead
from app.services.property_retrieval import PropertyRetrievalService


class WorkflowNotFoundError(LookupError):
    pass


class WorkflowAlreadyDecidedError(RuntimeError):
    pass


class ProposalValidationError(RuntimeError):
    pass


class WorkflowService:
    def __init__(
        self,
        retrieval_service: PropertyRetrievalService,
        agent: LeadProposalAgent,
        workflow_repo: WorkflowRepository,
    ) -> None:
        self._retrieval = retrieval_service
        self._agent = agent
        self._workflows = workflow_repo

    def ingest_lead(
        self,
        inbound: InboundLead,
        correlation_id: str,
        logger: SafeLoggerAdapter,
    ) -> Workflow:
        logger.info(
            "lead_webhook_received",
            status="received",
            lead_id=inbound.lead_id,
        )
        try:
            normalized = normalize_lead(inbound)
        except LeadNormalizationError as exc:
            logger.warning(
                "lead_normalized",
                status="rejected",
                lead_id=inbound.lead_id,
                reason=type(exc).__name__,
            )
            raise

        logger.info(
            "lead_normalized",
            status="ok",
            lead_id=normalized.lead_id,
            has_email=normalized.email is not None,
            has_phone=normalized.phone is not None,
        )

        retrieved = self._retrieval.retrieve(normalized)
        logger.info(
            "properties_retrieved",
            status="ok",
            lead_id=normalized.lead_id,
            retrieved_count=len(retrieved),
        )

        proposal = self._safe_generate_proposal(normalized, retrieved, logger)
        self._validate_proposal(proposal, retrieved)

        logger.info(
            "proposal_created",
            status="ok",
            lead_id=normalized.lead_id,
            recommended_count=len(proposal.recommended_property_ids),
        )

        workflow = Workflow(
            workflow_id=uuid4(),
            lead_id=normalized.lead_id,
            correlation_id=correlation_id,
            created_at=datetime.now(UTC),
            state=WorkflowState.PENDING_APPROVAL,
            retrieved_properties=retrieved,
            proposal=proposal,
            side_effect_executed=False,
            approval=None,
        )
        self._workflows.save(workflow)
        logger.info(
            "workflow_pending_approval",
            status="pending",
            workflow_id=str(workflow.workflow_id),
            lead_id=workflow.lead_id,
        )
        return workflow

    def _safe_generate_proposal(
        self,
        lead: NormalizedLead,
        properties: list[RankedProperty],
        logger: SafeLoggerAdapter,
    ) -> AgentProposal:
        try:
            return self._agent.generate_proposal(lead, properties)
        except LeadProposalAgentError as exc:
            logger.error(
                "workflow_error",
                status="agent_failure",
                lead_id=lead.lead_id,
                error_type=type(exc).__name__,
            )
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "workflow_error",
                message="Unexpected adapter failure",
                status="agent_unexpected_failure",
                lead_id=lead.lead_id,
                error_type=type(exc).__name__,
            )
            raise LeadProposalAgentError("Agent adapter failed unexpectedly.") from exc

    def _validate_proposal(
        self,
        proposal: AgentProposal,
        retrieved: list[RankedProperty],
    ) -> None:
        valid_ids = {rp.property_id for rp in retrieved}
        for pid in proposal.recommended_property_ids:
            if pid not in valid_ids:
                raise ProposalValidationError(
                    f"Proposal recommends unknown property id '{pid}' outside retrieved set."
                )
        if not proposal.requires_human_approval:
            raise ProposalValidationError("Proposal must require human approval.")

    def get_workflow(self, workflow_id: UUID) -> Workflow:
        stored = self._workflows.get(workflow_id)
        if stored is None:
            raise WorkflowNotFoundError(f"Workflow {workflow_id} not found.")
        return stored

    def decide(
        self,
        workflow_id: UUID,
        request: ApprovalRequest,
        logger: SafeLoggerAdapter,
    ) -> Workflow:
        workflow = self.get_workflow(workflow_id)
        if not can_transition(workflow.state, request.decision):
            raise WorkflowAlreadyDecidedError(
                f"Workflow in state '{workflow.state}' cannot accept "
                f"decision '{request.decision.value}'."
            )

        next_state = next_state_for(request.decision)
        approval = ApprovalMetadata(
            decided_at=datetime.now(UTC),
            decision=request.decision,
            approved_by=request.approved_by,
            comment=request.comment,
        )
        updated = workflow.model_copy(
            update={
                "state": next_state,
                "approval": approval,
                "side_effect_executed": False,
            }
        )
        self._workflows.save(updated)
        event = "workflow_approved" if request.decision.value == "approve" else "workflow_rejected"
        logger.info(
            event,
            status=next_state.value,
            workflow_id=str(workflow_id),
            lead_id=workflow.lead_id,
            approved_by=request.approved_by,
        )
        return updated


def summarize_workflow(workflow: Workflow) -> WorkflowSummary:
    return WorkflowSummary(
        workflow_id=workflow.workflow_id,
        lead_id=workflow.lead_id,
        correlation_id=workflow.correlation_id,
        state=workflow.state,
        side_effect_executed=workflow.side_effect_executed,
        approved_by=workflow.approval.approved_by if workflow.approval else None,
        decided_at=workflow.approval.decided_at if workflow.approval else None,
    )
