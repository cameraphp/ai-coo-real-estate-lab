from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    RootModel,
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
    """Approval body. The approver identity comes from the resolved demo principal."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: ApprovalDecision
    comment: str | None = Field(default=None, max_length=500)


class ApprovalMetadata(BaseModel):
    decided_at: datetime
    decision: ApprovalDecision
    approved_by: str
    comment: str | None = None


class Workflow(BaseModel):
    workflow_id: UUID
    tenant_id: str
    created_by: str
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
    tenant_id: str
    created_by: str
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


# --- Week 2: demo identity -------------------------------------------------


class Role(str, Enum):
    OPERATOR = "operator"
    APPROVER = "approver"
    VIEWER = "viewer"


class DemoPrincipal(BaseModel):
    """Local demo identity. Not production authentication."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_id: str
    tenant_id: str
    role: Role


# --- Week 2: mock tool contracts -------------------------------------------


class ToolName(str, Enum):
    """Canonical closed allowlist of mock tool names."""

    CRM_GET_LEAD_CONTEXT = "crm.get_lead_context"
    CALENDAR_FIND_AVAILABILITY = "calendar.find_availability"
    MESSAGING_CREATE_FOLLOW_UP_DRAFT = "messaging.create_follow_up_draft"
    CRM_CREATE_FOLLOW_UP_TASK = "crm.create_follow_up_task"


class ToolExecutionStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    REPLAYED = "REPLAYED"
    BLOCKED = "BLOCKED"
    DENIED = "DENIED"
    FAILED = "FAILED"


class ErrorCategory(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    TIMEOUT = "timeout"
    VALIDATION = "validation"
    AUTHORIZATION = "authorization"
    APPROVAL_REQUIRED = "approval_required"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    INTERNAL = "internal"


CALENDAR_MIN_DATE = date(2024, 1, 1)
CALENDAR_MAX_DATE = date(2099, 12, 31)


def _within_calendar_bounds(value: date) -> date:
    if not CALENDAR_MIN_DATE <= value <= CALENDAR_MAX_DATE:
        raise ValueError(
            f"requested_date must be between {CALENDAR_MIN_DATE} and {CALENDAR_MAX_DATE}"
        )
    return value


BoundedDate = Annotated[date, AfterValidator(_within_calendar_bounds)]


class _ToolInvocationBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: UUID


class CrmGetLeadContextInvocation(_ToolInvocationBase):
    tool_name: Literal[ToolName.CRM_GET_LEAD_CONTEXT]


class CalendarFindAvailabilityInvocation(_ToolInvocationBase):
    tool_name: Literal[ToolName.CALENDAR_FIND_AVAILABILITY]
    requested_date: BoundedDate
    duration_minutes: int = Field(strict=True, ge=15, le=120, multiple_of=15)


class MessagingCreateFollowUpDraftInvocation(_ToolInvocationBase):
    tool_name: Literal[ToolName.MESSAGING_CREATE_FOLLOW_UP_DRAFT]
    channel: Literal["email"]


class CrmCreateFollowUpTaskInvocation(_ToolInvocationBase):
    tool_name: Literal[ToolName.CRM_CREATE_FOLLOW_UP_TASK]
    due_in_hours: int = Field(strict=True, ge=1, le=72)


ToolInvocation = Annotated[
    CrmGetLeadContextInvocation
    | CalendarFindAvailabilityInvocation
    | MessagingCreateFollowUpDraftInvocation
    | CrmCreateFollowUpTaskInvocation,
    Field(discriminator="tool_name"),
]


class ToolInvocationRequest(RootModel[ToolInvocation]):
    """HTTP body wrapper so the discriminated union is validated as one body."""


class _ToolResultBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LeadContextResult(_ToolResultBase):
    kind: Literal["lead_context"] = "lead_context"
    crm_record_id: str
    lead_id: str
    lifecycle_stage: Literal["new_inquiry", "approved_for_follow_up", "closed_rejected"]
    intent: LeadIntent
    urgency: Urgency
    recommended_property_ids: list[str]
    contact_details_included: Literal[False] = False


class AvailabilitySlot(_ToolResultBase):
    start: datetime
    end: datetime


class AvailabilityResult(_ToolResultBase):
    kind: Literal["availability"] = "availability"
    requested_date: date
    duration_minutes: int
    timezone: Literal["UTC"] = "UTC"
    slots: list[AvailabilitySlot]
    booking_created: Literal[False] = False


class MessageDraftResult(_ToolResultBase):
    kind: Literal["message_draft"] = "message_draft"
    draft_id: str
    channel: Literal["email"]
    status: Literal["DRAFT"] = "DRAFT"
    sent: Literal[False] = False
    recipient_lead_id: str
    body_source: Literal["workflow.proposal.message_draft"] = "workflow.proposal.message_draft"
    body_length: int


class FollowUpTaskResult(_ToolResultBase):
    kind: Literal["follow_up_task"] = "follow_up_task"
    task_id: str
    title: str
    status: Literal["OPEN"] = "OPEN"
    due_at: datetime


ToolResultPayload = Annotated[
    LeadContextResult | AvailabilityResult | MessageDraftResult | FollowUpTaskResult,
    Field(discriminator="kind"),
]


class ToolExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID
    workflow_id: UUID
    tool_name: ToolName
    status: ToolExecutionStatus
    simulated: Literal[True] = True
    external_side_effect: Literal[False] = False
    attempts: int = Field(ge=1)
    idempotency_replayed: bool
    result: ToolResultPayload


# --- Week 2: audit trail ---------------------------------------------------


class AuditEventType(str, Enum):
    WORKFLOW_CREATED = "workflow_created"
    WORKFLOW_APPROVED = "workflow_approved"
    WORKFLOW_REJECTED = "workflow_rejected"
    WORKFLOW_DECISION_DENIED = "workflow_decision_denied"
    TOOL_EXECUTION_STARTED = "tool_execution_started"
    TOOL_EXECUTION_SUCCEEDED = "tool_execution_succeeded"
    TOOL_EXECUTION_REPLAYED = "tool_execution_replayed"
    TOOL_EXECUTION_BLOCKED_APPROVAL = "tool_execution_blocked_approval"
    TOOL_EXECUTION_DENIED = "tool_execution_denied"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"


class AuditOutcome(str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    REPLAYED = "replayed"
    DENIED = "denied"
    BLOCKED = "blocked"
    FAILED = "failed"
    CONFLICT = "conflict"


class AuditEvent(BaseModel):
    """Allowlisted audit fields only: no PII, raw arguments, draft bodies, or traces."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    occurred_at: datetime
    correlation_id: str
    tenant_id: str
    actor_id: str
    workflow_id: UUID
    event_type: AuditEventType
    outcome: AuditOutcome
    tool_name: ToolName | None = None
    execution_id: UUID | None = None
    idempotency_key_hash: str | None = None
    attempt: int | None = None
    error_category: ErrorCategory | None = None


class WorkflowAuditTrail(BaseModel):
    workflow_id: UUID
    events: list[AuditEvent]


# --- Error envelope -------------------------------------------------------


class ErrorResponse(BaseModel):
    correlation_id: str
    error: str
    detail: str | None = None
    error_category: ErrorCategory | None = None
    execution_id: UUID | None = None
    attempts: int | None = None
    fields: list[ValidationIssue] | None = None


class ErrorEnvelope(BaseModel):
    """Shape of every handled error body: ``{"detail": ErrorResponse}``."""

    detail: ErrorResponse


class ValidationIssue(BaseModel):
    """A request-validation problem without the rejected input value."""

    loc: list[str | int]
    msg: str
    type: str
