"""The public HTTP surface: one stateless MCP endpoint per scenario, read-only."""

import json
from pathlib import Path

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from rootcause.composition import telemetry_http_app

SCENARIOS = Path(__file__).parent.parent / "scenarios"


async def test_serves_every_scenario_read_only_over_http():
    app = telemetry_http_app(SCENARIOS)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://t") as http,
    ):
        health = (await http.get("/healthz")).json()
        assert health["status"] == "ok"
        assert len(health["scenarios"]) == len(list(SCENARIOS.glob("*/scenario.yaml")))

        url = f"http://t/scenarios/{health['scenarios'][0]}/mcp"
        async with (
            streamable_http_client(url, http_client=http) as (read, write, *_),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            assert "rollback_deploy" not in tools
            result = await session.call_tool("query_logs", {"limit": 2})
    assert not result.is_error
    assert len(result.content) == 2
    assert all(isinstance(c, TextContent) and "id" in json.loads(c.text) for c in result.content)
