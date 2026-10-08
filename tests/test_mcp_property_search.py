from __future__ import annotations

import pytest
from mcp import types
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError

from app.mcp.property_search import (
    PropertySearchInput,
    PropertySearchService,
)
from app.mcp.server import (
    MCP_TOOL_ALLOWLIST,
    SEARCH_TOOL_NAME,
    McpToolError,
    build_server,
    run_search_tool,
)
from app.observability.logging import new_logger
from app.repositories.properties import SyntheticJsonPropertyRepository
from app.security.demo_identity import resolve_demo_principal


def _service() -> PropertySearchService:
    return PropertySearchService(SyntheticJsonPropertyRepository())


def _server():
    return build_server(_service(), resolve_demo_principal("tenant-a-operator"), new_logger())


# --- Pure search service ----------------------------------------------------------


def test_search_returns_only_synthetic_properties_and_respects_max_results() -> None:
    result = _service().search(PropertySearchInput())
    assert result.synthetic_data_only is True
    assert len(result.properties) == 3
    assert result.total_matches >= len(result.properties)
    assert all(prop.is_synthetic is True for prop in result.properties)


def test_search_filters_by_city_bedrooms_and_price() -> None:
    result = _service().search(
        PropertySearchInput(city=" austin ", min_bedrooms=3, max_price=650_000, max_results=3)
    )
    assert [p.property_id for p in result.properties] == ["prop-demo-001", "prop-demo-002"]
    for prop in result.properties:
        assert prop.city == "Austin"
        assert prop.bedrooms >= 3
        assert prop.list_price <= 650_000


def test_search_is_deterministic_and_sorted_by_price() -> None:
    first = _service().search(PropertySearchInput(max_results=3))
    second = _service().search(PropertySearchInput(max_results=3))
    assert first == second
    prices = [p.list_price for p in first.properties]
    assert prices == sorted(prices)


def test_search_returns_empty_list_when_nothing_matches() -> None:
    result = _service().search(PropertySearchInput(city="Nowhere"))
    assert result.properties == []
    assert result.total_matches == 0


@pytest.mark.parametrize(
    "arguments",
    [
        {"max_results": 0},
        {"max_results": 4},
        {"max_results": "3"},
        {"min_bedrooms": "3"},
        {"min_bedrooms": 0},
        {"max_price": -1},
        {"max_price": True},
        {"city": ""},
        {"city": "../../etc/passwd"},
        {"city": "x" * 65},
        {"path": "/tmp/anything"},
    ],
)
def test_invalid_search_inputs_are_rejected(arguments: dict) -> None:
    with pytest.raises(ValidationError):
        PropertySearchInput.model_validate(arguments)
    with pytest.raises(McpToolError):
        run_search_tool(_service(), SEARCH_TOOL_NAME, arguments)


def test_unknown_mcp_tool_is_rejected() -> None:
    with pytest.raises(McpToolError):
        run_search_tool(_service(), "write_crm_record", {})


# --- MCP server over an in-memory session --------------------------------------------


def test_server_registers_only_list_and_call_tool_handlers() -> None:
    handlers = set(_server().request_handlers)
    assert types.ListToolsRequest in handlers
    assert types.CallToolRequest in handlers
    for absent in (
        types.ListResourcesRequest,
        types.ReadResourceRequest,
        types.ListPromptsRequest,
        types.GetPromptRequest,
        types.SetLevelRequest,
        types.CompleteRequest,
    ):
        assert absent not in handlers


async def test_server_lists_exactly_one_read_only_tool() -> None:
    async with create_connected_server_and_client_session(_server()) as session:
        listed = await session.list_tools()
    assert [tool.name for tool in listed.tools] == [SEARCH_TOOL_NAME]
    assert sorted(MCP_TOOL_ALLOWLIST) == [SEARCH_TOOL_NAME]
    tool = listed.tools[0]
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.openWorldHint is False
    assert tool.inputSchema["additionalProperties"] is False
    assert tool.inputSchema["properties"]["max_results"]["maximum"] == 3


async def test_server_call_returns_structured_synthetic_results() -> None:
    async with create_connected_server_and_client_session(_server()) as session:
        result = await session.call_tool(SEARCH_TOOL_NAME, {"city": "Phoenix", "max_results": 1})
    assert result.isError is False
    assert result.structuredContent is not None
    properties = result.structuredContent["properties"]
    assert len(properties) == 1
    assert properties[0]["city"] == "Phoenix"
    assert properties[0]["is_synthetic"] is True


async def test_server_call_rejects_invalid_arguments_and_unknown_tools() -> None:
    async with create_connected_server_and_client_session(_server()) as session:
        too_many = await session.call_tool(SEARCH_TOOL_NAME, {"max_results": 10})
        extra = await session.call_tool(SEARCH_TOOL_NAME, {"command": "rm -rf /"})
        unknown = await session.call_tool("run_shell", {"command": "whoami"})
    assert too_many.isError is True
    assert extra.isError is True
    assert unknown.isError is True
