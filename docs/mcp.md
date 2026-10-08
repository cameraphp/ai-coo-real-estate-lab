# Local MCP Server — `search_synthetic_properties`

This repository ships a deliberately small [Model Context Protocol](https://modelcontextprotocol.io) server built on the official Python SDK (`mcp` 1.12.x). It is a **portfolio demonstration of a narrow, read-only tool boundary**, not a production MCP deployment.

## Start it

```bash
python -m app.mcp.server   # or: make mcp
```

The process speaks MCP over **stdio only**. It opens no network port. An MCP client launches it as a subprocess, for example with this client configuration:

```json
{
  "mcpServers": {
    "ai-coo-synthetic-properties": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "/path/to/ai-coo-real-estate-lab"
    }
  }
}
```

Use the Python interpreter from the project's virtual environment.

## Tool allowlist

Exactly one tool is listed and callable:

| Tool | Annotations |
| --- | --- |
| `search_synthetic_properties` | `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false` |

Any other tool name returns an MCP error result (`isError: true`). The server registers only the `tools/list` and `tools/call` handlers, with no resources, prompts, completions, or logging-level control.

## Input schema

Generated from `PropertySearchInput` in `app/mcp/property_search.py`, with `additionalProperties: false`:

| Field | Type | Constraint | Default |
| --- | --- | --- | --- |
| `city` | string or null | 1–64 chars, letters, spaces, `.`, `'`, `-`; case-insensitive exact match | no filter |
| `min_bedrooms` | integer or null | 1..10 (strict integer) | no filter |
| `max_price` | integer or null | 1..100,000,000 (strict integer) | no filter |
| `max_results` | integer | 1..3 (strict integer) | 3 |

Validation runs twice: the SDK checks the arguments against the JSON Schema, and the strict Pydantic model checks them again. Invalid arguments return `isError: true` with a short message naming the rejected fields.

## Output

Structured content, also validated against the advertised `outputSchema`:

```json
{
  "synthetic_data_only": true,
  "total_matches": 2,
  "properties": [
    {
      "property_id": "prop-demo-001",
      "city": "Austin",
      "neighborhood": "Demo District North",
      "list_price": 599000,
      "bedrooms": 3,
      "bathrooms": 2,
      "property_type": "single_family",
      "summary": "Synthetic three-bedroom home ...",
      "is_synthetic": true
    }
  ]
}
```

Results are sorted by `(list_price, property_id)` and truncated to `max_results`. When nothing matches, `properties` is an empty list.

## Data boundary

- The only data source is `data/properties.synthetic.json`, which is already public in this repository and labeled `is_synthetic: true`.
- The server has no access to workflows, leads, the audit trail, idempotency records, or the CRM, calendar, and messaging mocks. It cannot approve anything or change any state.
- The search service (`PropertySearchService`) is a pure filter over the loaded catalog. It makes no network calls, filesystem writes, or shell calls, and executes no code from its inputs.

## Logging

All logs go to **stderr**, because stdout is reserved for MCP protocol messages. Each tool call logs `actor_id`, `tenant_id`, `tool_name`, `status`, and `result_count`, never the arguments.

## `MCP_DEMO_ACTOR_ID`

At startup the server resolves `MCP_DEMO_ACTOR_ID` (default `tenant-a-operator`) through the same static demo-identity table as the HTTP API, and refuses to start if the value is unknown. The resolved actor and tenant appear only as **log labels**.

This is **not** authentication or authorization. Over stdio, whoever can start the process can call the tool. The catalog is not tenant-scoped, because it is shared synthetic demo data.

## Why this is not production MCP security

- No transport authentication, because there is no network transport at all.
- No per-call identity; the actor label is process-wide and self-declared.
- No rate limiting, quotas, or resource limits beyond the tiny bounded inputs.
- No sandboxing beyond the fact that the tool does nothing but filter an in-memory list.

These limits are acceptable here because the tool is read-only and the data is synthetic and already public in this repository. Exposing anything sensitive or state-changing over MCP would need real authentication, per-request authorization, tenant scoping, and audit, which are out of scope for this repository.

## Verification

- `tests/test_mcp_property_search.py` covers the pure service (filters, bounds, empty results, determinism), typed input rejection, the registered handler set, and full protocol round trips through the SDK's in-memory client/server session (`tools/list` and `tools/call`).
- A manual stdio smoke test using the SDK's own `stdio_client` (no extra tooling) started `python -m app.mcp.server` as a subprocess and confirmed that one tool is listed, that a valid search returns structured results, that an empty search returns `[]`, and that an out-of-range value, an extra field, and an unknown tool name each return `isError: true`.
