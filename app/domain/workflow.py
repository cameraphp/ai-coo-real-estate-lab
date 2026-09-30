from __future__ import annotations

from app.domain.models import ApprovalDecision, WorkflowState

ALLOWED_TRANSITIONS: dict[WorkflowState, set[ApprovalDecision]] = {
    WorkflowState.PENDING_APPROVAL: {
        ApprovalDecision.APPROVE,
        ApprovalDecision.REJECT,
    },
    WorkflowState.APPROVED: set(),
    WorkflowState.REJECTED: set(),
}

DECISION_TO_STATE: dict[ApprovalDecision, WorkflowState] = {
    ApprovalDecision.APPROVE: WorkflowState.APPROVED,
    ApprovalDecision.REJECT: WorkflowState.REJECTED,
}


def can_transition(current: WorkflowState, decision: ApprovalDecision) -> bool:
    return decision in ALLOWED_TRANSITIONS.get(current, set())


def next_state_for(decision: ApprovalDecision) -> WorkflowState:
    return DECISION_TO_STATE[decision]


def is_decided(state: WorkflowState) -> bool:
    return state in {WorkflowState.APPROVED, WorkflowState.REJECTED}
