"""Adapts tools served over MCP to the application's `Tool` port."""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp import Client
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as McpToolInfo

from rootcause.application.ports import Block, ToolResult


def render(result: CallToolResult) -> str:
    """Tool output for the model: the structured payload as JSON when there is one."""
    if result.structured_content is not None:
        payload: Any = result.structured_content
        if set(payload) == {"result"}:
            payload = payload["result"]
        return json.dumps(payload, default=str)
    return "\n".join(b.text for b in result.content if isinstance(b, TextContent))


@dataclass(frozen=True)
class McpTool:
    client: Client
    info: McpToolInfo
    read_only: bool = False
    idempotent: bool = False

    @property
    def name(self) -> str:
        return self.info.name

    @property
    def definition(self) -> Block:
        return {
            "name": self.info.name,
            "description": self.info.description or "",
            "input_schema": self.info.input_schema,
        }

    async def invoke(self, args: dict[str, Any]) -> ToolResult:
        result = await self.client.call_tool(self.name, args)
        return ToolResult(render(result), is_error=result.is_error)


@asynccontextmanager
async def mcp_tools(
    target: Any,
    *,
    read_only: frozenset[str] = frozenset(),
    idempotent: frozenset[str] = frozenset(),
) -> AsyncGenerator[list[McpTool]]:
    """Connect to an MCP server (an in-process server object, a URL, or stdio
    parameters) and yield its tools for the length of the block.

    `read_only` names the tools the caller vouches for. Everything else is gated: a
    server's `read_only_hint` is its own claim, and the gate can't rest on that.
    `idempotent` names gated tools the caller says are safe to run twice; the rest run
    at most once, whatever the server's `idempotent_hint` says. Read-only tools always are."""
    async with Client(target) as client:
        listing = await client.list_tools()
        safe = read_only | idempotent
        yield [
            McpTool(client, info, info.name in read_only, info.name in safe)
            for info in listing.tools
        ]
