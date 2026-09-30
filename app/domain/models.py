from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
)


def _lowercase_email(v: str) -> str:
    return v.lower()


LowercaseEmailStr = Annotated[EmailStr, AfterValidator(_lowercase_email)]


class LeadIntent(str, Enum):
    BUY = "buy"
    SELL = "sell"
    RENT = "rent"
    UNKNOWN = "unknown"


class Urgency(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProposedAction(str, Enum):
    DRAFT_FOLLOW_UP = "draft_follow_up"


class WorkflowState(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PropertyType(str, Enum):
    SINGLE_FAMILY = "single_family"
    TOWNHOUSE = "townhouse"
    CONDO = "condo"


class InboundLead(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    lead_id: str = Field(min_length=1)
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    email: EmailStr | None = None
    phone: str | None = None
    preferred_city: str = Field(min_length=1)
    budget_max: int = Field(gt=0)
    min_bedrooms: int = Field(ge=1, le=20)
    message: str | None = None


class NormalizedLead(BaseModel):
    lead_id: str
    first_name: str
    last_name: str
    email: LowercaseEmailStr | None = None
    phone: str | None = None
    preferred_city: str
    budget_max: int
    min_bedrooms: int
    has_message: bool


class SyntheticProperty(BaseModel):
    property_id: str
    city: str
    neighborhood: str
    list_price: int
    bedrooms: int
    bathrooms: int
    property_type: PropertyType
    summary: str
    is_synthetic: Literal[True]


class RankedProperty(SyntheticProperty):
    rank_score: float
    rank_reason: str


class AgentProposal(BaseModel):
    intent: LeadIntent
    urgency: Urgency
    recommended_property_ids: list[str]
    proposed_action: ProposedAction = ProposedAction.DRAFT_FOLLOW_UP
    message_draft: str
    rationale: str
    requires_human_approval: Literal[True] = True


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalRequest(BaseModel):
    decision: ApprovalDecision
    approved_by: str = Field(min_length=1)
    comment: str | None = None


class ApprovalMetadata(BaseModel):
    decided_at: datetime
    decision: ApprovalDecision
    approved_by: str
    comment: str | None = None


class Workflow(BaseModel):
    workflow_id: UUID
    lead_id: str
    correlation_id: str
    created_at: datetime
    state: WorkflowState
    retrieved_properties: list[RankedProperty]
    proposal: AgentProposal
    side_effect_executed: bool = False
    approval: ApprovalMetadata | None = None


class WorkflowSummary(BaseModel):
    workflow_id: UUID
    lead_id: str
    correlation_id: str
    state: WorkflowState
    side_effect_executed: bool
    approved_by: str | None = None
    decided_at: datetime | None = None


class WebhookResponse(BaseModel):
    correlation_id: str
    workflow: WorkflowSummary
    retrieved_count: int
    side_effect_executed: bool = False
    message: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["ai-coo-real-estate-lab"] = "ai-coo-real-estate-lab"
    agent_provider: str


class ErrorResponse(BaseModel):
    correlation_id: str
    error: str
    detail: str | None = None
