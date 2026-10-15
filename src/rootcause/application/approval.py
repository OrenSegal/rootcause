"""Human approval for tool calls that change the world.

The gate lives in the agent loop, not the MCP server: the 2026-07-28 MCP
protocol removed server-initiated requests, so a server can no longer stop
mid-call to ask the user. The loop also sees every call before it is sent,
which is the only place a gate can't be bypassed by a misbehaving server.
"""

from typing import Any

from rootcause.application.ports import Decision


class StaticApproval:
    """A fixed answer. Evals use this so each scenario's operator is reproducible."""

    def __init__(self, approve: bool) -> None:
        self.approve = approve

    async def decide(self, tool: str, args: dict[str, Any]) -> Decision:
        return Decision(approved=self.approve, by="policy:static")
