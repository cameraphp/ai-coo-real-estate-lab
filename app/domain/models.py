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
    field_validator,
    model_validator,
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


# --- Week 3: secure RAG ------------------------------------------------------

RAG_QUESTION_MAX_CHARS = 500
RAG_TOP_K_LIMIT = 5
RAG_EXCERPT_MAX_CHARS = 280
RAG_EMBEDDING_LABEL = "local-deterministic-hashed-lexical"


class DocumentType(str, Enum):
    PROPERTY_BRIEF = "property_brief"
    TOUR_POLICY = "tour_policy"
    FOLLOW_UP_PROCEDURE = "follow_up_procedure"
    ELIGIBILITY_NOTE = "eligibility_note"
    CONVERSATION_SUMMARY = "conversation_summary"
    INBOUND_NOTE = "inbound_note"


class RerankerKind(str, Enum):
    """Server-owned reranker choice. Never accepted from a client."""

    NONE = "none"
    LEXICAL = "lexical"


class RagAnswerStatus(str, Enum):
    GROUNDED = "GROUNDED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class InsufficientEvidenceReason(str, Enum):
    NO_RELEVANT_EVIDENCE = "no_relevant_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    UNTRUSTED_INSTRUCTION_DETECTED = "untrusted_instruction_detected"
    QUERY_INJECTION_DETECTED = "query_injection_detected"


SourceDocumentId = Annotated[
    str, Field(min_length=5, max_length=64, pattern=r"^doc-[a-z0-9]+(?:-[a-z0-9]+)*$")
]
TenantId = Annotated[str, Field(min_length=8, max_length=32, pattern=r"^tenant-[a-z0-9]+$")]
CityName = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z .'-]*$")]
SyntheticPropertyId = Annotated[str, Field(pattern=r"^prop-demo-[0-9]{3}$")]


class CorpusDocument(BaseModel):
    """One record of the committed synthetic corpus fixture."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    source_document_id: SourceDocumentId
    tenant_id: TenantId
    title: str = Field(min_length=1, max_length=120)
    document_type: DocumentType
    city: CityName | None = None
    property_id: SyntheticPropertyId | None = None
    content: str = Field(min_length=1, max_length=4000)
    is_synthetic: Literal[True]
    # Explicit tags for the controlled contradiction demonstration.
    fact_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{2,63}$")
    fact_value: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")

    @model_validator(mode="after")
    def _fact_tags_are_paired(self) -> CorpusDocument:
        if (self.fact_key is None) != (self.fact_value is None):
            raise ValueError("fact_key and fact_value must be provided together")
        return self


class SyntheticCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    corpus_version: str = Field(pattern=r"^v[0-9]+$")
    description: str = Field(min_length=1, max_length=500)
    documents: list[CorpusDocument] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _document_ids_are_unique(self) -> SyntheticCorpus:
        ids = [document.source_document_id for document in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("source_document_id values must be unique")
        return self


class RagFilters(BaseModel):
    """The complete, typed filter surface. There is no filter expression language."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    city: CityName | None = None
    document_type: DocumentType | None = None
    property_id: SyntheticPropertyId | None = None

    def is_empty(self) -> bool:
        return self.city is None and self.document_type is None and self.property_id is None


class RagQueryRequest(BaseModel):
    """RAG query body. The tenant comes from the resolved demo principal, never from here."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=RAG_QUESTION_MAX_CHARS)
    top_k: int = Field(default=3, strict=True, ge=1, le=RAG_TOP_K_LIMIT)
    filters: RagFilters | None = None

    @field_validator("question")
    @classmethod
    def _collapse_whitespace(cls, value: str) -> str:
        return " ".join(value.split())


class RagCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_document_id: str
    chunk_id: str
    title: str
    document_type: DocumentType
    chunk_index: int = Field(ge=0)
    excerpt: str = Field(min_length=1, max_length=RAG_EXCERPT_MAX_CHARS)
    retrieval_score: float
    rerank_score: float | None = None


class RagClaim(BaseModel):
    """One statement of a grounded answer, paired with the chunks that contain it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=RAG_EXCERPT_MAX_CHARS)
    citation_chunk_ids: list[str] = Field(min_length=1)


class RagRetrievalSummary(BaseModel):
    """Safe observability summary. Counts are tenant-scoped; vectors are never included."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reranker: RerankerKind
    embedding: Literal["local-deterministic-hashed-lexical"] = RAG_EMBEDDING_LABEL
    top_k: int
    filters_applied: bool
    filtered_candidate_count: int = Field(ge=0)
    candidate_pool_count: int = Field(ge=0)
    excluded_untrusted_count: int = Field(ge=0)
    returned_citation_count: int = Field(ge=0)
    latency_ms: float = Field(ge=0)


class RagQueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: UUID
    correlation_id: str
    answer_status: RagAnswerStatus
    insufficient_evidence_reason: InsufficientEvidenceReason | None = None
    answer: str
    claims: list[RagClaim]
    citations: list[RagCitation]
    retrieval: RagRetrievalSummary
    synthetic_data_only: Literal[True] = True

    @model_validator(mode="after")
    def _answer_is_evidence_bound(self) -> RagQueryResponse:
        cited = {citation.chunk_id for citation in self.citations}
        if self.answer_status is RagAnswerStatus.INSUFFICIENT_EVIDENCE:
            if self.insufficient_evidence_reason is None:
                raise ValueError("an insufficient-evidence response requires a reason")
            if self.claims or self.citations:
                raise ValueError("an insufficient-evidence response carries no evidence")
            return self
        if self.insufficient_evidence_reason is not None:
            raise ValueError("a grounded response has no insufficient-evidence reason")
        if not self.claims or not self.citations:
            raise ValueError("a grounded response requires claims and citations")
        for claim in self.claims:
            if not set(claim.citation_chunk_ids) <= cited:
                raise ValueError("every claim must reference returned citations")
        return self


class RagIndexStatus(BaseModel):
    """Tenant-scoped index summary. It never reports another tenant's counts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    corpus_version: str
    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    redaction_count: int = Field(ge=0)
    quarantined_document_count: int = Field(ge=0)
    embedding: Literal["local-deterministic-hashed-lexical"] = RAG_EMBEDDING_LABEL
    embedding_dimensions: int
    chunk_size_tokens: int
    chunk_overlap_tokens: int
    reranker: RerankerKind
    storage: Literal["in_memory"] = "in_memory"
    synthetic_data_only: Literal[True] = True


class RagAuditEventType(str, Enum):
    QUERY_RECEIVED = "rag_query_received"
    QUERY_TENANT_SCOPED = "rag_query_tenant_scoped"
    QUERY_GROUNDED = "rag_query_grounded"
    QUERY_INSUFFICIENT_EVIDENCE = "rag_query_insufficient_evidence"


class RagAuditEvent(BaseModel):
    """Allowlisted RAG audit fields: identifiers and statuses only, never content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    occurred_at: datetime
    correlation_id: str
    tenant_id: str
    actor_id: str
    query_id: UUID
    event_type: RagAuditEventType
    answer_status: RagAnswerStatus | None = None
    insufficient_evidence_reason: InsufficientEvidenceReason | None = None


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
