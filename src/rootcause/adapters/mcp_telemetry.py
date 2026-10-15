"""MCP server exposing one scenario's telemetry as tools.

The annotations matter: the agent loop reads them to decide which calls are
safe to retry and which need a human. They are hints from the server, so the
loop gates anything not explicitly read-only.
"""

from datetime import datetime

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from rootcause.application.ports import Retriever
from rootcause.domain.postmortems import Hit
from rootcause.domain.store import TelemetryStore
from rootcause.domain.telemetry import (
    ConfigChange,
    Deploy,
    LogLevel,
    LogLine,
    MetricSeries,
    Span,
    SpanStatus,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)


class RollbackResult(BaseModel):
    action_id: str
    deploy_id: str
    already_rolled_back: bool


def build_telemetry_server(store: TelemetryStore, retriever: Retriever | None = None) -> MCPServer:
    mcp = MCPServer("rootcause-telemetry")

    def require_deploy(deploy_id: str) -> None:
        if store.get_deploy(deploy_id) is None:
            raise ToolError(f"No deploy with id {deploy_id!r}.")

    @mcp.tool(annotations=READ_ONLY)
    def query_logs(
        service: str | None = None,
        level: LogLevel | None = None,
        contains: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
    ) -> list[LogLine]:
        """Search log lines, oldest first. Filter by service, level, a case-insensitive
        substring, and an ISO-8601 time window. Each line has an id you can cite."""
        return store.query_logs(service, level, contains, since, until, min(limit, 200))

    @mcp.tool(annotations=READ_ONLY)
    def query_metrics(service: str, name: str | None = None) -> list[MetricSeries]:
        """Return metric time series for a service (all series, or one by name)."""
        return store.query_metrics(service, name)

    @mcp.tool(annotations=READ_ONLY)
    def search_traces(
        service: str,
        min_duration_ms: float = 0,
        status: SpanStatus | None = None,
        limit: int = 10,
    ) -> list[Span]:
        """Find root spans for a service, slowest first. Use get_trace for the full tree."""
        return store.search_traces(service, min_duration_ms, status, min(limit, 50))

    @mcp.tool(annotations=READ_ONLY)
    def get_trace(trace_id: str) -> list[Span]:
        """Return every span in a trace, ordered by start time."""
        spans = store.get_trace(trace_id)
        if not spans:
            raise ToolError(f"No trace with id {trace_id!r}.")
        return spans

    @mcp.tool(annotations=READ_ONLY)
    def list_deploys(
        service: str | None = None, since: datetime | None = None, until: datetime | None = None
    ) -> list[Deploy]:
        """List deploys across services, oldest first, optionally filtered by service and time."""
        return store.list_deploys(service, since, until)

    @mcp.tool(annotations=READ_ONLY)
    def diff_config(deploy_id: str) -> list[ConfigChange]:
        """Return the configuration keys a deploy changed, with before and after values."""
        require_deploy(deploy_id)
        return store.diff_config(deploy_id)

    @mcp.tool(annotations=DESTRUCTIVE)
    def rollback_deploy(deploy_id: str, reason: str) -> RollbackResult:
        """Roll a deploy back to the previous version. This changes production and
        requires operator approval. Calling it twice for the same deploy is a no-op."""
        require_deploy(deploy_id)
        action_id, created = store.rollback(deploy_id)
        return RollbackResult(
            action_id=action_id, deploy_id=deploy_id, already_rolled_back=not created
        )

    if retriever is not None:

        @mcp.tool(annotations=READ_ONLY)
        def search_postmortems(query: str, k: int = 3) -> list[Hit]:
            """Search past incident postmortems by description. Returns the closest
            write-ups with their root cause and fix. They describe earlier incidents,
            not this one, so cite telemetry ids as evidence, not postmortem ids."""
            return retriever.search(query, min(k, 10))

    return mcp
