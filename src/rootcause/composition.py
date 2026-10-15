"""Composition root: the one module that wires adapters to the application.

Interfaces (CLI, MCP server) call these functions and never construct adapters
themselves, so each wiring decision is made in one place.
"""

import asyncio
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from mcp.server import MCPServer
from pydantic import BaseModel

from rootcause.adapters import anthropic_provider, heuristic
from rootcause.adapters.claude_code import ClaudeCodeHarness
from rootcause.adapters.fixtures import (
    SCENARIO_FILE,
    discover,
    load_case,
    load_scenario,
    write_case,
)
from rootcause.adapters.fixtures import (
    load_sweep_spec as load_sweep_spec,  # re-exported: the CLI may not import adapters
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
from rootcause.application.sweep import (
    ConfigSummary,
    Row,
    SweepConfig,
    SweepSpec,
    frontier,
    is_error,
    report_markdown,
    summarize,
)
from rootcause.application.transcript import TOOL_CALL
from rootcause.domain.answers import Diagnosis
from rootcause.domain.scenario import Case, Scenario

PRICES = {**anthropic_provider.PRICES, **heuristic.PRICES}


class ProviderName(StrEnum):
    HEURISTIC = "heuristic"
    ANTHROPIC = "anthropic"
    CLAUDE_CODE = "claude-code"  # Claude Code runs the loop; single agent only


class EffortLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


# What runs an investigation: a model behind the hand-written loop, or a harness
# that brings its own loop.
Engine = LLMProvider | ClaudeCodeHarness
ProviderFactory = Callable[[], Engine]


def provider_factory(name: ProviderName, model: str, effort: EffortLevel) -> ProviderFactory:
    def make() -> Engine:
        if name is ProviderName.HEURISTIC:
            return heuristic.HeuristicProvider()
        if name is ProviderName.CLAUDE_CODE:
            return ClaudeCodeHarness(model=model, effort=effort.value)
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


SINGLE_AGENT_ONLY = {ProviderName.HEURISTIC, ProviderName.CLAUDE_CODE}


def supports(provider_name: str, mode: Mode) -> bool:
    """The heuristic only knows the single-agent tool protocol, and Claude Code
    runs its own loop, so neither can be an orchestrator here."""
    return mode is Mode.SINGLE or provider_name.split(":")[0] not in SINGLE_AGENT_ONLY


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


async def _run(
    case: Case, engine: Engine, events: EventLog, budget_usd: float, mode: Mode, approve: bool
) -> RunResult[Diagnosis]:
    if isinstance(engine, ClaudeCodeHarness):
        if not supports(engine.name, mode):
            raise ValueError(UNSUPPORTED.format(provider=engine.name))
        return await engine.investigate(case, events=events, approve=approve, budget_usd=budget_usd)
    return await _investigate(case, _loop(engine, events, budget_usd, approve), mode)


async def investigate(
    case: Case,
    provider: Engine,
    *,
    events: EventLog,
    budget_usd: float,
    mode: Mode = Mode.SINGLE,
    approve: bool | None = None,
) -> Investigation:
    allow = case.scenario.approval == "approve" if approve is None else approve
    result = await _run(case, provider, events, budget_usd, mode, allow)
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
    provider: Engine,
    *,
    events: EventLog,
    budget_usd: float,
    mode: Mode = Mode.SINGLE,
) -> RunResult[Diagnosis]:
    """Investigate a real failed CI run. There is no ground truth, so nothing is graded,
    and the operator always denies: triage never changes anything."""
    return await _run(case, provider, events, budget_usd, mode, approve=False)


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


ROWS, ERRORS, SPEC = "rows.jsonl", "errors.jsonl", "spec.json"
EngineFactory = Callable[[SweepConfig], Engine]


def sweep_engine(config: SweepConfig) -> Engine:
    """Validates the names in a sweep file against the ones this build knows."""
    return provider_factory(
        ProviderName(config.provider), config.model, EffortLevel(config.effort)
    )()


def read_sweep(out: Path) -> tuple[list[Row], list[Row]]:
    def load(name: str) -> list[Row]:
        path = out / name
        if not path.exists():
            return []
        return [Row.model_validate_json(line) for line in path.read_text().splitlines() if line]

    return load(ROWS), load(ERRORS)


def _start_sweep(spec: SweepSpec, out: Path) -> set[tuple[str, str, int]]:
    out.mkdir(parents=True, exist_ok=True)
    (out / SPEC).write_text(spec.model_dump_json(indent=2))
    return {(r.config, r.scenario_id, r.rep) for r in read_sweep(out)[0]}


def _append(path: Path, line: str) -> None:
    with path.open("a") as f:
        f.write(line + "\n")


async def run_sweep(
    spec: SweepSpec,
    out: Path,
    *,
    make_engine: EngineFactory = sweep_engine,
    concurrency: int = 1,
) -> dict[str, int]:
    """Every (repeat, scenario, config) not already in rows.jsonl. Configs are interleaved
    within each scenario and repeat, so rate limits or time of day don't fall on one
    config. Errors go to errors.jsonl and are retried by the next call."""
    done = _start_sweep(spec, out)
    events = open_events(out / "runs.sqlite")
    paths = discover(Path(spec.scenarios))
    by_name = {c.name: c for c in spec.configs}
    todo = [
        (rep, path, config)
        for rep in range(spec.repeats)
        for path in paths
        for config in spec.configs
    ]
    gate, write = asyncio.Semaphore(concurrency), asyncio.Lock()
    counts = {"ran": 0, "skipped": 0}

    async def one(rep: int, path: Path, config: SweepConfig) -> None:
        case = load_case(path)
        if (config.name, case.scenario.id, rep) in done:
            counts["skipped"] += 1
            return
        async with gate:
            run = await investigate(
                case,
                make_engine(config),
                events=events,
                budget_usd=config.budget_usd,
                mode=Mode(config.mode),
            )
        row = Row(config=config.name, scenario_id=case.scenario.id, rep=rep, grade=run.grade)
        target = out / (ERRORS if is_error(by_name[row.config], row) else ROWS)
        async with write:
            await asyncio.to_thread(_append, target, row.model_dump_json())
            counts["ran"] += 1

    await asyncio.gather(*(one(*t) for t in todo))
    return counts


def summarize_sweep(out: Path) -> tuple[SweepSpec, list[ConfigSummary], list[Row]]:
    spec = SweepSpec.model_validate_json((out / SPEC).read_text())
    rows, errors = read_sweep(out)
    summaries = [
        summarize(
            c,
            [r for r in rows if r.config == c.name],
            [r for r in errors if r.config == c.name],
        )
        for c in spec.configs
    ]
    return spec, frontier(summaries), rows


def plan_sweep(spec: SweepSpec, out: Path) -> tuple[int, int]:
    """(runs planned, runs already done) without starting anything."""
    total = spec.repeats * len(discover(Path(spec.scenarios))) * len(spec.configs)
    return total, len(read_sweep(out)[0])


def write_sweep_report(out: Path) -> str:
    spec, summaries, rows = summarize_sweep(out)
    md = report_markdown(spec, summaries, rows)
    (out / "report.md").write_text(md)
    (out / "summary.json").write_text(
        "[" + ",\n".join(s.model_dump_json() for s in summaries) + "]\n"
    )
    return md
