from mcp import Client
from mcp.types import TextContent

from rootcause.adapters.mcp_telemetry import build_telemetry_server


async def test_lists_tools_with_annotations(store):
    async with Client(build_telemetry_server(store)) as client:
        listing = await client.list_tools()
    tools = {t.name: t for t in listing.tools}
    assert set(tools) == {
        "query_logs",
        "query_metrics",
        "search_traces",
        "get_trace",
        "list_deploys",
        "diff_config",
        "rollback_deploy",
    }
    read, rollback = tools["query_logs"].annotations, tools["rollback_deploy"].annotations
    assert read is not None and read.read_only_hint is True
    assert rollback is not None and rollback.read_only_hint is False
    assert rollback.destructive_hint is True


async def test_query_logs_returns_structured_lines(store):
    async with Client(build_telemetry_server(store)) as client:
        result = await client.call_tool("query_logs", {"service": "checkout", "level": "error"})
    assert not result.is_error
    assert result.structured_content is not None
    lines = result.structured_content["result"]
    assert lines and all(line["level"] == "error" for line in lines)
    assert "pool" in lines[0]["message"]


async def test_unknown_deploy_is_a_tool_error_the_model_can_read(store):
    async with Client(build_telemetry_server(store)) as client:
        result = await client.call_tool("diff_config", {"deploy_id": "d-nope"})
    assert result.is_error
    block = result.content[0]
    assert isinstance(block, TextContent) and "d-nope" in block.text


async def test_rollback_is_idempotent(store):
    async with Client(build_telemetry_server(store)) as client:
        first = await client.call_tool("rollback_deploy", {"deploy_id": "d-1042", "reason": "x"})
        second = await client.call_tool("rollback_deploy", {"deploy_id": "d-1042", "reason": "x"})
    assert first.structured_content and second.structured_content
    assert first.structured_content["already_rolled_back"] is False
    assert second.structured_content["already_rolled_back"] is True
    assert store.rollbacks == {"d-1042": "rb-d-1042"}
