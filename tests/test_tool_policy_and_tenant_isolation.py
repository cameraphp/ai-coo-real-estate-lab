from __future__ import annotations

import pytest

from app.domain.models import DemoPrincipal, Role, ToolName, WorkflowState
from app.security.demo_identity import (
    DEMO_PRINCIPALS,
    UnknownDemoActorError,
    resolve_demo_principal,
)
from app.security.tool_policy import (
    ROLE_CAPABILITIES,
    TOOL_ALLOWLIST,
    ApprovalRequiredError,
    AuthorizationError,
    Capability,
    authorize_tool,
    ensure_approval_satisfied,
)
from tests.support import (
    APPROVER_A,
    DEMO_LEAD,
    OPERATOR_A,
    OPERATOR_B,
    VIEWER_A,
    actor,
    approve,
    calendar_body,
    context_body,
    counting_adapters,
    create_workflow,
    draft_body,
    execute_tool,
    make_client,
    task_body,
)

# --- Static identity and policy tables --------------------------------------


def test_demo_principals_match_documented_mapping() -> None:
    assert {k: (p.tenant_id, p.role) for k, p in DEMO_PRINCIPALS.items()} == {
        "tenant-a-operator": ("tenant-a", Role.OPERATOR),
        "tenant-a-approver": ("tenant-a", Role.APPROVER),
        "tenant-a-viewer": ("tenant-a", Role.VIEWER),
        "tenant-b-operator": ("tenant-b", Role.OPERATOR),
    }


@pytest.mark.parametrize("actor_id", [None, "", "tenant-c-operator", "TENANT-A-OPERATOR"])
def test_unknown_demo_actor_is_rejected(actor_id: str | None) -> None:
    with pytest.raises(UnknownDemoActorError):
        resolve_demo_principal(actor_id)


def test_tool_allowlist_covers_exactly_the_canonical_tool_names() -> None:
    assert set(TOOL_ALLOWLIST) == set(ToolName)
    assert {t.value for t in ToolName} == {
        "crm.get_lead_context",
        "calendar.find_availability",
        "messaging.create_follow_up_draft",
        "crm.create_follow_up_task",
    }


def test_only_the_mock_crm_task_requires_an_approved_workflow() -> None:
    gated = {name for name, policy in TOOL_ALLOWLIST.items() if policy.requires_approved_workflow}
    assert gated == {ToolName.CRM_CREATE_FOLLOW_UP_TASK}


def test_role_capability_matrix() -> None:
    assert ROLE_CAPABILITIES[Role.OPERATOR] == {
        Capability.CREATE_WORKFLOW,
        Capability.READ_WORKFLOW,
        Capability.INVOKE_TOOLS,
    }
    assert ROLE_CAPABILITIES[Role.APPROVER] == set(Capability)
    assert ROLE_CAPABILITIES[Role.VIEWER] == {Capability.READ_WORKFLOW}


@pytest.mark.parametrize("tool_name", list(ToolName))
def test_viewer_is_denied_every_tool(tool_name: ToolName) -> None:
    viewer = DemoPrincipal(actor_id="v", tenant_id="t", role=Role.VIEWER)
    with pytest.raises(AuthorizationError):
        authorize_tool(viewer, tool_name)


def test_approval_rule_blocks_until_approved() -> None:
    policy = TOOL_ALLOWLIST[ToolName.CRM_CREATE_FOLLOW_UP_TASK]
    for state in (WorkflowState.PENDING_APPROVAL, WorkflowState.REJECTED):
        with pytest.raises(ApprovalRequiredError):
            ensure_approval_satisfied(policy, state)
    ensure_approval_satisfied(policy, WorkflowState.APPROVED)


# --- HTTP identity ------------------------------------------------------------


def test_health_stays_public() -> None:
    response = make_client().get("/health")
    assert response.status_code == 200


@pytest.mark.parametrize("headers", [{}, {"X-Demo-Actor-ID": "nobody"}])
def test_missing_or_unknown_actor_returns_401_on_protected_endpoints(
    headers: dict[str, str],
) -> None:
    client = make_client()
    workflow_id = create_workflow(client)
    cid = {"X-Correlation-ID": "cid-401-check"}
    responses = [
        client.post("/api/v1/leads/webhook", json=DEMO_LEAD, headers={**headers, **cid}),
        client.get(f"/api/v1/workflows/{workflow_id}", headers={**headers, **cid}),
        client.post(
            f"/api/v1/workflows/{workflow_id}/approval",
            json={"decision": "approve"},
            headers={**headers, **cid},
        ),
        client.get(f"/api/v1/workflows/{workflow_id}/audit", headers={**headers, **cid}),
        client.post(
            "/api/v1/tools/execute",
            json=context_body(workflow_id),
            headers={**headers, **cid, "Idempotency-Key": "key-401-check"},
        ),
    ]
    for response in responses:
        assert response.status_code == 401, response.text
        assert response.json()["detail"]["error"] == "unknown_demo_actor"
        assert response.headers["X-Correlation-ID"] == "cid-401-check"


def test_workflow_binds_tenant_and_creator_from_principal() -> None:
    client = make_client()
    response = client.post(
        "/api/v1/leads/webhook",
        json=DEMO_LEAD,
        # A client-supplied tenant header must be ignored.
        headers=actor(OPERATOR_A, **{"X-Tenant-ID": "tenant-b"}),
    )
    assert response.status_code == 200, response.text
    summary = response.json()["workflow"]
    assert summary["tenant_id"] == "tenant-a"
    assert summary["created_by"] == "tenant-a-operator"

    workflow = client.get(
        f"/api/v1/workflows/{summary['workflow_id']}", headers=actor(OPERATOR_A)
    ).json()
    assert workflow["tenant_id"] == "tenant-a"
    assert workflow["created_by"] == "tenant-a-operator"


def test_viewer_cannot_create_workflow() -> None:
    response = make_client().post("/api/v1/leads/webhook", json=DEMO_LEAD, headers=actor(VIEWER_A))
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"


# --- Tenant isolation ---------------------------------------------------------


def test_other_tenant_gets_404_for_read_approve_tool_and_audit() -> None:
    adapters, crm, calendar, messaging = counting_adapters()
    client = make_client(tool_adapters=adapters)
    workflow_id = create_workflow(client)

    read = client.get(f"/api/v1/workflows/{workflow_id}", headers=actor(OPERATOR_B))
    decide = client.post(
        f"/api/v1/workflows/{workflow_id}/approval",
        json={"decision": "approve"},
        headers=actor(OPERATOR_B),
    )
    tool = execute_tool(client, context_body(workflow_id), "tenant-b-probe-1", OPERATOR_B)
    audit = client.get(f"/api/v1/workflows/{workflow_id}/audit", headers=actor(OPERATOR_B))

    for response in (read, decide, tool, audit):
        assert response.status_code == 404, response.text
        body = response.json()["detail"]
        assert body["error"] == "workflow_not_found"
        assert "tenant-a" not in response.text
        assert "lead-week2-001" not in response.text
    assert crm.context_calls == 0

    still_pending = client.get(f"/api/v1/workflows/{workflow_id}", headers=actor(OPERATOR_A))
    assert still_pending.json()["state"] == "PENDING_APPROVAL"


def test_cross_tenant_probe_is_not_written_to_owner_audit_trail() -> None:
    client = make_client()
    workflow_id = create_workflow(client)
    execute_tool(client, context_body(workflow_id), "tenant-b-probe-2", OPERATOR_B)
    events = client.get(f"/api/v1/workflows/{workflow_id}/audit", headers=actor(OPERATOR_A)).json()[
        "events"
    ]
    assert all(e["actor_id"] != OPERATOR_B for e in events)


# --- Role enforcement over HTTP -------------------------------------------------


@pytest.mark.parametrize(
    "body_factory", [context_body, calendar_body, draft_body, task_body], ids=lambda f: f.__name__
)
def test_viewer_tool_invocation_returns_403_and_is_audited(body_factory) -> None:
    adapters, crm, calendar, messaging = counting_adapters()
    client = make_client(tool_adapters=adapters)
    workflow_id = create_workflow(client)

    response = execute_tool(client, body_factory(workflow_id), "viewer-attempt-1", VIEWER_A)

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error_category"] == "authorization"
    assert (crm.context_calls, crm.task_calls, calendar.calls, messaging.calls) == (0, 0, 0, 0)
    events = client.get(f"/api/v1/workflows/{workflow_id}/audit", headers=actor(VIEWER_A)).json()[
        "events"
    ]
    denied = [e for e in events if e["event_type"] == "tool_execution_denied"]
    assert len(denied) == 1
    assert denied[0]["actor_id"] == VIEWER_A
    assert denied[0]["outcome"] == "denied"
    assert denied[0]["error_category"] == "authorization"


@pytest.mark.parametrize("actor_id", [OPERATOR_A, VIEWER_A])
def test_only_approver_can_decide(actor_id: str) -> None:
    client = make_client()
    workflow_id = create_workflow(client)
    response = client.post(
        f"/api/v1/workflows/{workflow_id}/approval",
        json={"decision": "approve"},
        headers=actor(actor_id),
    )
    assert response.status_code == 403, response.text
    workflow = client.get(f"/api/v1/workflows/{workflow_id}", headers=actor(OPERATOR_A)).json()
    assert workflow["state"] == "PENDING_APPROVAL"
    assert workflow["approval"] is None


def test_approval_identity_comes_from_principal() -> None:
    client = make_client()
    workflow_id = create_workflow(client)
    body = approve(client, workflow_id).json()
    assert body["approval"]["approved_by"] == APPROVER_A
    assert body["side_effect_executed"] is False


def test_approval_body_cannot_supply_an_approver_identity() -> None:
    client = make_client()
    workflow_id = create_workflow(client)
    response = client.post(
        f"/api/v1/workflows/{workflow_id}/approval",
        json={"decision": "approve", "approved_by": "someone-else"},
        headers=actor(APPROVER_A),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "request_validation_failed"


def test_viewer_can_read_own_tenant_workflow_and_audit() -> None:
    client = make_client()
    workflow_id = create_workflow(client)
    assert (
        client.get(f"/api/v1/workflows/{workflow_id}", headers=actor(VIEWER_A)).status_code == 200
    )
    audit = client.get(f"/api/v1/workflows/{workflow_id}/audit", headers=actor(VIEWER_A))
    assert audit.status_code == 200
