"""Composition root: the one module that wires adapters to the application.

Interfaces (CLI, MCP server) call these functions and never construct adapters
themselves, so each wiring decision is made in one place.
"""

from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from mcp.server import MCPServer
from pydantic import BaseModel

from rootcause.adapters import anthropic_provider, heuristic
from rootcause.adapters.fixtures import (
    SCENARIO_FILE,
    discover,
    load_case,
    load_scenario,
    write_case,
)
from rootcause.adapters.github_actions import GhCli, RawRun, case_from_raw
from rootcause.adapters.mcp_telemetry import build_telemetry_server
from rootcause.adapters.mcp_tools import mcp_tools
from rootcause.adapters.sqlite_events import SqliteEventLog
from rootcause.application.agent import AgentLoop, RunResult
from rootcause.application.approval import StaticApproval
from rootcause.application.budget import Budget
from rootcause.application.evals import Grade, SuiteReport, grade
from rootcause.application.investigator import investigator_spec
from rootcause.application.orchestrator import DispatchWorkers, orchestrator_spec
from rootcause.application.ports import EventLog, LLMProvider
from rootcause.application.transcript import TOOL_CALL
from rootcause.domain.answers import Diagnosis
from rootcause.domain.scenario import Case, Scenario

PRICES = {**anthropic_provider.PRICES, **heuristic.PRICES}


class ProviderName(StrEnum):
    HEURISTIC = "heuristic"
    ANTHROPIC = "anthropic"


class EffortLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


ProviderFactory = Callable[[], LLMProvider]


def provider_factory(name: ProviderName, model: str, effort: EffortLevel) -> ProviderFactory:
    def make() -> LLMProvider:
        if name is ProviderName.HEURISTIC:
            return heuristic.HeuristicProvider()
        return anthropic_provider.AnthropicProvider(model=model, effort=effort.value)

    return make


def open_events(path: Path | None = None) -> EventLog:
    """The event log: a SQLite file, or in memory when no path is given."""
    if path is None:
        return SqliteEventLog()
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteEventLog(path)


def list_scenarios(root: Path) -> list[Scenario]:
    return [load_scenario(path) for path in discover(root)]


def find_case(root: Path, scenario_id: str) -> Case | None:
    path = root / scenario_id
    return load_case(path) if (path / SCENARIO_FILE).exists() else None


def telemetry_server(scenario_dir: Path) -> MCPServer:
    return build_telemetry_server(load_case(scenario_dir).store)


class Investigation(BaseModel):
    result: RunResult[Diagnosis]
    grade: Grade


class Mode(StrEnum):
    SINGLE = "single"  # one agent, every tool
    MULTI = "multi"  # orchestrator plus read-only workers


UNSUPPORTED = "{provider} has no multi-agent mode; it only speaks the single-agent tools."


def supports(provider_name: str, mode: Mode) -> bool:
    """The heuristic only knows the single-agent tool protocol."""
    return not (mode is Mode.MULTI and provider_name == ProviderName.HEURISTIC)


def count_tool_calls(events: EventLog, run_id: str) -> int:
    """Tool calls in a run and every run it started (workers)."""
    own = sum(1 for e in events.events(run_id) if e.kind == TOOL_CALL)
    return own + sum(count_tool_calls(events, child.run_id) for child in events.children(run_id))


def _loop(provider: LLMProvider, events: EventLog, budget_usd: float, approve: bool) -> AgentLoop:
    return AgentLoop(
        provider=provider,
        events=events,
        budget=Budget(limit_usd=budget_usd, prices=PRICES),
        approval=StaticApproval(approve),
    )


async def _investigate(case: Case, loop: AgentLoop, mode: Mode) -> RunResult[Diagnosis]:
    if not supports(loop.provider.name, mode):
        raise ValueError(UNSUPPORTED.format(provider=loop.provider.name))
    scenario = case.scenario
    async with mcp_tools(build_telemetry_server(case.store)) as tools:
        if mode is Mode.SINGLE:
            spec, agent_tools = investigator_spec(), list(tools)
        else:
            dispatcher = DispatchWorkers(loop, tools, scenario_id=scenario.id)
            spec, agent_tools = orchestrator_spec(), [*tools, dispatcher]
        return await loop.run(spec, scenario.alert.render(), agent_tools, scenario_id=scenario.id)


async def investigate(
    case: Case,
    provider: LLMProvider,
    *,
    events: EventLog,
    budget_usd: float,
    mode: Mode = Mode.SINGLE,
    approve: bool | None = None,
) -> Investigation:
    allow = case.scenario.approval == "approve" if approve is None else approve
    result = await _investigate(case, _loop(provider, events, budget_usd, allow), mode)
    graded = grade(
        case,
        result,
        provider=provider.name,
        mode=mode.value,
        tool_calls=count_tool_calls(events, result.run_id),
    )
    return Investigation(result=result, grade=graded)


async def run_suite(
    root: Path,
    make_provider: ProviderFactory,
    *,
    events: EventLog,
    budget_usd: float,
    mode: Mode = Mode.SINGLE,
) -> SuiteReport:
    grades: list[Grade] = []
    name = make_provider().name
    for path in discover(root):
        run = await investigate(
            load_case(path), make_provider(), events=events, budget_usd=budget_usd, mode=mode
        )
        grades.append(run.grade)
    return SuiteReport(provider=name, mode=mode.value, grades=grades)


async def compare(
    root: Path,
    factories: list[ProviderFactory],
    modes: list[Mode],
    *,
    events: EventLog,
    budget_usd: float,
) -> tuple[list[SuiteReport], list[str]]:
    """Every provider in every mode it supports, on the same scenarios."""
    reports: list[SuiteReport] = []
    notes: list[str] = []
    for make in factories:
        name = make().name
        for mode in modes:
            if not supports(name, mode):
                notes.append(UNSUPPORTED.format(provider=name))
                continue
            reports.append(
                await run_suite(root, make, events=events, budget_usd=budget_usd, mode=mode)
            )
    return reports, notes


async def triage_ci(
    case: Case,
    provider: LLMProvider,
    *,
    events: EventLog,
    budget_usd: float,
    mode: Mode = Mode.SINGLE,
) -> RunResult[Diagnosis]:
    """Investigate a real failed CI run. There is no ground truth, so nothing is graded,
    and the operator always denies: triage never changes anything."""
    return await _investigate(case, _loop(provider, events, budget_usd, approve=False), mode)


def load_ci_raw(raw_dir: Path) -> Case:
    """A previously recorded run, offline."""
    return case_from_raw(RawRun.load(raw_dir))


def fetch_ci_case(repo: str, run_id: int, record_to: Path | None = None) -> Case:
    """Read-only `gh` calls. With `record_to`, saves the raw responses and the derived
    fixtures as a scenario directory (add `truth` by hand to use it in evals)."""
    raw = GhCli().fetch(repo, run_id)
    case = case_from_raw(raw)
    if record_to is not None:
        out = record_to / case.scenario.id
        raw.save(out / "raw")
        write_case(case, out)
    return case
