"""Local, read-only stdio MCP server over the synthetic property fixture.

Run with ``python -m app.mcp.server``. It exposes exactly one tool,
``search_synthetic_properties``. It has no network listener, no write-capable
tools, and no access to workflows, CRM, calendar, messaging, or approvals.
stdout carries only MCP protocol messages; all logs go to stderr.

``MCP_DEMO_ACTOR_ID`` labels the process in logs and must be a known demo actor.
It is not authentication: whoever can start the process can use the tool.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.models import DemoPrincipal
from app.mcp.property_search import PropertySearchInput, PropertySearchResult, PropertySearchService
from app.observability.logging import SafeLoggerAdapter, configure_logging, new_logger
from app.repositories.properties import SyntheticJsonPropertyRepository
from app.security.demo_identity import resolve_demo_principal

SERVER_NAME = "ai-coo-synthetic-properties"
SEARCH_TOOL_NAME = "search_synthetic_properties"
MCP_TOOL_ALLOWLIST = frozenset({SEARCH_TOOL_NAME})


class McpSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    log_level: str = "INFO"
    mcp_demo_actor_id: str = "tenant-a-operator"
    properties_catalog_path: Path | None = None


class McpToolError(ValueError):
    """Safe, client-visible tool error (returned to the client as ``isError``)."""


def search_tool_definition() -> types.Tool:
    return types.Tool(
        name=SEARCH_TOOL_NAME,
        title="Search synthetic properties",
        description=(
            "Read-only search over the repository's fully synthetic property catalog. "
            "Filters: city, min_bedrooms, max_price; at most 3 results."
        ),
        inputSchema=PropertySearchInput.model_json_schema(),
        outputSchema=PropertySearchResult.model_json_schema(),
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )


def run_search_tool(
    search: PropertySearchService, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    if name not in MCP_TOOL_ALLOWLIST:
        raise McpToolError("Unknown tool. Only search_synthetic_properties is available.")
    try:
        query = PropertySearchInput.model_validate(arguments)
    except ValidationError as exc:
        fields = ", ".join(".".join(str(p) for p in err["loc"]) or "input" for err in exc.errors())
        raise McpToolError(f"Invalid arguments: {fields}") from exc
    return search.search(query).model_dump(mode="json")


def build_server(
    search: PropertySearchService,
    principal: DemoPrincipal,
    logger: SafeLoggerAdapter,
) -> Server:
    server: Server = Server(SERVER_NAME)
    log = logger.with_context(actor_id=principal.actor_id, tenant_id=principal.tenant_id)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [search_tool_definition()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            result = run_search_tool(search, name, arguments)
        except McpToolError:
            log.warning("mcp_tool_rejected", status="rejected", tool_name=name[:64])
            raise
        log.info(
            "mcp_tool_called",
            status="ok",
            tool_name=name,
            result_count=len(result["properties"]),
        )
        return result

    return server


async def serve(settings: McpSettings) -> None:
    configure_logging(settings.log_level, stream=sys.stderr)
    principal = resolve_demo_principal(settings.mcp_demo_actor_id)
    logger = new_logger()
    search = PropertySearchService(
        SyntheticJsonPropertyRepository(settings.properties_catalog_path)
    )
    server = build_server(search, principal, logger)
    logger.info("mcp_server_started", status="ok", actor_id=principal.actor_id, transport="stdio")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    asyncio.run(serve(McpSettings()))


if __name__ == "__main__":
    main()
