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

    @property
    def read_only(self) -> bool:
        return bool(self.info.annotations and self.info.annotations.read_only_hint)

    @property
    def idempotent(self) -> bool:
        ann = self.info.annotations
        return self.read_only or bool(ann and ann.idempotent_hint)

    async def invoke(self, args: dict[str, Any]) -> ToolResult:
        result = await self.client.call_tool(self.name, args)
        return ToolResult(render(result), is_error=result.is_error)


@asynccontextmanager
async def mcp_tools(target: Any) -> AsyncGenerator[list[McpTool]]:
    """Connect to an MCP server (an in-process server object, a URL, or stdio
    parameters) and yield its tools for the length of the block."""
    async with Client(target) as client:
        listing = await client.list_tools()
        yield [McpTool(client, info) for info in listing.tools]
