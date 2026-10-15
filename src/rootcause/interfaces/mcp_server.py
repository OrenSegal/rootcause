"""rootcause as an MCP server, for coding agents.

Register with Claude Code:
    claude mcp add rootcause -- uv run --directory /path/to/rootcause rootcause mcp
"""

from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from rootcause.application.evals import SuiteReport
from rootcause.application.ports import EventLog
from rootcause.composition import (
    Investigation,
    ProviderFactory,
    find_case,
    investigate,
    run_suite,
)
from rootcause.composition import (
    list_scenarios as scenarios_in,
)

# Runs spend model budget and write the event log, so they are not read-only,
# but they never touch anything outside the simulated scenario.
RUNS = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)


class ScenarioSummary(BaseModel):
    id: str
    title: str


def build_agent_server(
    scenarios_root: Path, make_provider: ProviderFactory, events: EventLog, budget_usd: float
) -> MCPServer:
    mcp = MCPServer("rootcause")

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
    def list_scenarios() -> list[ScenarioSummary]:
        """List the incident scenarios this server can investigate."""
        return [ScenarioSummary(id=s.id, title=s.title) for s in scenarios_in(scenarios_root)]

    @mcp.tool(annotations=RUNS)
    async def investigate_incident(scenario_id: str) -> Investigation:
        """Run the investigator on one scenario. Returns the diagnosis, every tool call,
        cost, and a grade against the scenario's known root cause."""
        case = find_case(scenarios_root, scenario_id)
        if case is None:
            raise ToolError(f"No scenario {scenario_id!r}. Call list_scenarios.")
        return await investigate(case, make_provider(), events=events, budget_usd=budget_usd)

    @mcp.tool(annotations=RUNS)
    async def run_evals() -> SuiteReport:
        """Run every scenario and return the pass rate, per-scenario grades and total cost."""
        return await run_suite(scenarios_root, make_provider, events=events, budget_usd=budget_usd)

    return mcp
