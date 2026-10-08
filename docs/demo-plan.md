# Demo-Plan Script — 3–5 Minute Walkthrough

> **Do not record a video yet.** Recording is Week 4 work. This document is a script for a future recording session. It can also be read aloud as a live walkthrough during interviews or portfolio reviews.

All commands assume `make run` in terminal A and the shell variables from the README's [`curl` walkthrough](../README.md#curl-walkthrough) in terminal B:

```bash
BASE=http://127.0.0.1:8000
JSON='Content-Type: application/json'
OPERATOR='X-Demo-Actor-ID: tenant-a-operator'
APPROVER='X-Demo-Actor-ID: tenant-a-approver'
VIEWER='X-Demo-Actor-ID: tenant-a-viewer'
TENANT_B='X-Demo-Actor-ID: tenant-b-operator'
SHOW='\nHTTP %{http_code}\n'
```

## Timing

| Step | Duration |
|------|----------|
| 1. Business problem and disclaimer | 0:00–0:25 |
| 2. Architecture and boundaries | 0:25–0:55 |
| 3. Synthetic lead webhook (Week 1 core) | 0:55–1:25 |
| 4. Allowed mock tool action | 1:25–1:50 |
| 5. Tool denial for the viewer role | 1:50–2:10 |
| 6. High-impact action blocked until approval | 2:10–2:35 |
| 7. Approval, then safe mock execution | 2:35–3:10 |
| 8. Idempotency replay and conflict | 3:10–3:40 |
| 9. Tenant isolation | 3:40–3:55 |
| 10. Audit trail | 3:55–4:25 |
| 11. MCP server mention, limitations, next steps | 4:25–5:00 |

## 1. Business problem and disclaimer

> "Real-estate operations teams receive inbound leads that need validating, matching to properties, and a follow-up, with a human in the loop. This repository is a controlled engineering demonstration of that workflow. All data is synthetic. CRM, calendar, and messaging are local in-memory mocks. Nothing is sent, booked, or written to a real system."

Show the README disclaimer.

## 2. Architecture and boundaries

Narrate over the README Mermaid diagram:

> "A static demo actor header resolves to a tenant and a role. That is not real authentication, just a visible boundary. Tool calls go through a closed allowlist, strict schemas, tenant-scoped idempotency, an approval rule for the one high-impact mock write, and a bounded executor. Every decision lands in an append-only audit trail."

## 3. Synthetic lead webhook

```bash
WORKFLOW_ID=$(curl -s -X POST $BASE/api/v1/leads/webhook -H "$JSON" -H "$OPERATOR" \
  -H 'X-Correlation-ID: demo-video-001' \
  -d '{"lead_id":"lead-video-001","first_name":"Avery","last_name":"Stone",
       "email":"avery.stone@example.com","phone":"+1 (555) 010-0101",
       "preferred_city":"Austin","budget_max":650000,"min_bedrooms":3,
       "message":"Looking for a family home near parks. I can tour this weekend."}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['workflow']['workflow_id'])")
curl -s $BASE/api/v1/workflows/$WORKFLOW_ID -H "$OPERATOR" | python -m json.tool
```

> "The workflow is bound to tenant-a and its creator, with three synthetic matches and a mock proposal marked NOT SENT. It is waiting at PENDING_APPROVAL, and side_effect_executed is false."

## 4. Allowed mock tool action

```bash
curl -s -w "$SHOW" -X POST $BASE/api/v1/tools/execute -H "$JSON" -H "$OPERATOR" \
  -H 'Idempotency-Key: video-draft-001' \
  -d '{"tool_name":"messaging.create_follow_up_draft","workflow_id":"'"$WORKFLOW_ID"'","channel":"email"}'
```

> "201. A draft record now exists in memory with status DRAFT and sent false. The response says simulated true and external_side_effect false. The client cannot choose a recipient or the message text."

## 5. Tool denial

```bash
curl -s -w "$SHOW" -X POST $BASE/api/v1/tools/execute -H "$JSON" -H "$VIEWER" \
  -H 'Idempotency-Key: video-viewer-01' \
  -d '{"tool_name":"crm.get_lead_context","workflow_id":"'"$WORKFLOW_ID"'"}'
```

> "The viewer can read but cannot invoke tools, so this is a 403. The denial is audited too."

## 6. High-impact action blocked until approval

```bash
curl -s -w "$SHOW" -X POST $BASE/api/v1/tools/execute -H "$JSON" -H "$OPERATOR" \
  -H 'Idempotency-Key: video-task-0001' \
  -d '{"tool_name":"crm.create_follow_up_task","workflow_id":"'"$WORKFLOW_ID"'","due_in_hours":24}'
```

> "The mock CRM task is the one high-impact operation. While the workflow is pending, it returns 409 approval_required and no task is created."

## 7. Approval, then safe mock execution

```bash
curl -s -w "$SHOW" -X POST $BASE/api/v1/workflows/$WORKFLOW_ID/approval -H "$JSON" -H "$APPROVER" \
  -d '{"decision":"approve","comment":"Reviewed in local demo"}'
curl -s -w "$SHOW" -X POST $BASE/api/v1/tools/execute -H "$JSON" -H "$OPERATOR" \
  -H 'Idempotency-Key: video-task-0001' \
  -d '{"tool_name":"crm.create_follow_up_task","workflow_id":"'"$WORKFLOW_ID"'","due_in_hours":24}'
```

> "Only the approver role can decide, and approved_by comes from the resolved identity, not from the request body. Once the workflow is approved, the same request succeeds with 201. It is still simulated, with no external side effect, and the workflow's side_effect_executed stays false."

## 8. Idempotency replay and conflict

```bash
# identical repeat
curl -s -w "$SHOW" -X POST $BASE/api/v1/tools/execute -H "$JSON" -H "$OPERATOR" \
  -H 'Idempotency-Key: video-task-0001' \
  -d '{"tool_name":"crm.create_follow_up_task","workflow_id":"'"$WORKFLOW_ID"'","due_in_hours":24}'
# same key, changed arguments
curl -s -w "$SHOW" -X POST $BASE/api/v1/tools/execute -H "$JSON" -H "$OPERATOR" \
  -H 'Idempotency-Key: video-task-0001' \
  -d '{"tool_name":"crm.create_follow_up_task","workflow_id":"'"$WORKFLOW_ID"'","due_in_hours":48}'
```

> "The identical request replays with a 200, the same execution_id, and REPLAYED. The adapter did not run again, so there is still exactly one mock task. Changing the arguments under the same key returns 409 idempotency_key_reused."

## 9. Tenant isolation

```bash
curl -s -w "$SHOW" $BASE/api/v1/workflows/$WORKFLOW_ID/audit -H "$TENANT_B"
```

> "Tenant B gets a 404, which is identical to a workflow that doesn't exist. Other tenants' data isn't even confirmed to exist."

## 10. Audit trail

```bash
curl -s $BASE/api/v1/workflows/$WORKFLOW_ID/audit -H "$OPERATOR" | python -m json.tool
```

> "Chronological evidence: created, draft started and succeeded, viewer denied, task blocked, approved, task started and succeeded, replayed, conflict. Each event has the actor, tenant, correlation ID, tool, attempt, and error category. There's no email, phone, message text, draft body, or raw arguments. The idempotency key appears only as a salted hash."

## 11. MCP server, limitations, and next steps

> "There is also a small local stdio MCP server, started with `make mcp`. It has exactly one read-only tool that searches the synthetic catalog, with strict bounded inputs and logs on stderr only."

> "What this is not: no real authentication, no real integrations, no durable storage, single process only, and the timeout is cooperative. Week 3 adds RAG and evaluation. Week 4 adds Docker, CI, and this recording."

Optional failure-path callout if time allows: "Transient, permanent, and timeout adapter failures are covered by deterministic tests. Only transient errors retry, bounded at two attempts, and a timeout never commits a mock record." Show `tests/test_tool_executor_resilience.py` briefly.
