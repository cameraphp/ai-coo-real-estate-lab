"""Role capabilities, the closed tool allowlist, and the human-approval rule."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from app.domain.models import DemoPrincipal, Role, ToolName, WorkflowState


class Capability(str, Enum):
    CREATE_WORKFLOW = "create_workflow"
    READ_WORKFLOW = "read_workflow"
    DECIDE_WORKFLOW = "decide_workflow"
    INVOKE_TOOLS = "invoke_tools"


ROLE_CAPABILITIES: Mapping[Role, frozenset[Capability]] = MappingProxyType(
    {
        Role.OPERATOR: frozenset(
            {Capability.CREATE_WORKFLOW, Capability.READ_WORKFLOW, Capability.INVOKE_TOOLS}
        ),
        Role.APPROVER: frozenset(
            {
                Capability.CREATE_WORKFLOW,
                Capability.READ_WORKFLOW,
                Capability.DECIDE_WORKFLOW,
                Capability.INVOKE_TOOLS,
            }
        ),
        Role.VIEWER: frozenset({Capability.READ_WORKFLOW}),
    }
)


class ToolImpact(str, Enum):
    READ = "read"
    DRAFT = "draft"
    MOCK_WRITE = "mock_write"


@dataclass(frozen=True)
class ToolPolicy:
    tool_name: ToolName
    impact: ToolImpact
    allowed_roles: frozenset[Role]
    requires_approved_workflow: bool


_TOOL_ROLES = frozenset({Role.OPERATOR, Role.APPROVER})

TOOL_ALLOWLIST: Mapping[ToolName, ToolPolicy] = MappingProxyType(
    {
        ToolName.CRM_GET_LEAD_CONTEXT: ToolPolicy(
            ToolName.CRM_GET_LEAD_CONTEXT, ToolImpact.READ, _TOOL_ROLES, False
        ),
        ToolName.CALENDAR_FIND_AVAILABILITY: ToolPolicy(
            ToolName.CALENDAR_FIND_AVAILABILITY, ToolImpact.READ, _TOOL_ROLES, False
        ),
        ToolName.MESSAGING_CREATE_FOLLOW_UP_DRAFT: ToolPolicy(
            ToolName.MESSAGING_CREATE_FOLLOW_UP_DRAFT, ToolImpact.DRAFT, _TOOL_ROLES, False
        ),
        ToolName.CRM_CREATE_FOLLOW_UP_TASK: ToolPolicy(
            ToolName.CRM_CREATE_FOLLOW_UP_TASK, ToolImpact.MOCK_WRITE, _TOOL_ROLES, True
        ),
    }
)


class AuthorizationError(PermissionError):
    pass


class ApprovalRequiredError(RuntimeError):
    pass


def has_capability(principal: DemoPrincipal, capability: Capability) -> bool:
    return capability in ROLE_CAPABILITIES.get(principal.role, frozenset())


def require_capability(principal: DemoPrincipal, capability: Capability) -> None:
    if not has_capability(principal, capability):
        raise AuthorizationError(
            f"Role '{principal.role.value}' lacks capability '{capability.value}'."
        )


def authorize_tool(principal: DemoPrincipal, tool_name: ToolName) -> ToolPolicy:
    """Return the tool's policy if the principal may invoke it; raise otherwise."""
    require_capability(principal, Capability.INVOKE_TOOLS)
    policy = TOOL_ALLOWLIST.get(tool_name)
    if policy is None:
        raise AuthorizationError("Tool is not on the server-side allowlist.")
    if principal.role not in policy.allowed_roles:
        raise AuthorizationError(
            f"Role '{principal.role.value}' may not invoke '{tool_name.value}'."
        )
    return policy


def ensure_approval_satisfied(policy: ToolPolicy, workflow_state: WorkflowState) -> None:
    if policy.requires_approved_workflow and workflow_state != WorkflowState.APPROVED:
        raise ApprovalRequiredError(
            f"'{policy.tool_name.value}' requires an APPROVED workflow; "
            f"current state is {workflow_state.value}."
        )
