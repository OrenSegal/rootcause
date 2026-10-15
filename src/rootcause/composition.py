"""Composition root: the one module that wires adapters to the application.

Interfaces (CLI, MCP server) call these functions and never construct adapters
themselves, so each wiring decision is made in one place.
"""

import asyncio
import time
from collections.abc import Callable
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Literal

from mcp.server import MCPServer
from pydantic import BaseModel

from rootcause.adapters import anthropic_provider, heuristic
from rootcause.adapters.claude_code import ClaudeCodeHarness
from rootcause.adapters.corpus import load_corpus, load_qrels
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
from rootcause.application.agent import AgentLoop, RunResult, TokenUsage
from rootcause.application.approval import StaticApproval
from rootcause.application.budget import Budget
from rootcause.application.evals import Grade, SuiteReport, grade
from rootcause.application.investigator import investigator_spec
from rootcause.application.orchestrator import DispatchWorkers, orchestrator_spec
from rootcause.application.ports import EventLog, LLMProvider, Retriever, Tracer
from rootcause.application.retrieval_eval import RetrievalReport, evaluate
from rootcause.application.sweep import (
    ConfigSummary,
    Retrieval,
    Row,
    SweepConfig,
    SweepSpec,
    frontier,
    is_error,
    report_markdown,
    summarize,
)
from rootcause.application.tracing import NullTracer
from rootcause.application.transcript import OUTCOME, TOOL_CALL
from rootcause.domain.answers import Diagnosis
from rootcause.domain.scenario import Case, Scenario

PRICES = {**anthropic_provider.PRICES, **heuristic.PRICES}


class TraceExporter(StrEnum):
    NONE = "none"
    CONSOLE = "console"
    OTLP = "otlp"


class _Tracing:
    """The process-wide tracer. Off unless `configure_tracing` is called."""

    tracer: Tracer = NullTracer()


_tracing = _Tracing()


def configure_tracing(exporter: TraceExporter) -> None:
    if exporter is TraceExporter.NONE:
        return
    try:
        from rootcause.adapters import otel
    except ImportError as e:
        raise RuntimeError("tracing needs the otel extra: uv sync --extra otel") from e
    _tracing.tracer = otel.configure(exporter.value)


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
            return ClaudeCodeHarness(model=model, effort=effort.value, tracer=_tracing.tracer)
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


CORPUS = Path("corpus")


@cache
def make_retriever(mode: Retrieval, corpus: Path = CORPUS) -> Retriever | None:
    """One index per mode and process: dense modes load a model and embed the corpus."""
    if mode == "none":
        return None
    from rootcause.adapters import retrieval  # embedders import their extras lazily

    docs = load_corpus(corpus)
    keyword = retrieval.BM25Retriever(docs)
    match mode:
        case "bm25":
            return keyword
        case "static":
            return retrieval.DenseRetriever(docs, retrieval.StaticEmbedder())
        case "dense":
            return retrieval.DenseRetriever(docs, retrieval.TransformerEmbedder())
        case "hybrid":
            dense = retrieval.DenseRetriever(docs, retrieval.TransformerEmbedder())
            return retrieval.HybridRetriever(keyword, dense)


def telemetry_server(
    scenario_dir: Path, retrieval: Retrieval = Retrieval.NONE, corpus: Path = CORPUS
) -> MCPServer:
    return build_telemetry_server(load_case(scenario_dir).store, make_retriever(retrieval, corpus))


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


def worker_spend(events: EventLog, run_id: str) -> tuple[float, TokenUsage]:
    """Cost and tokens of every run this one started (workers), read from their logged
    outcomes. The shared budget already caps them; the grade has to count them too."""
    cost, usage = 0.0, TokenUsage()
    for child in events.children(run_id):
        for e in events.events(child.run_id):
            if e.kind == OUTCOME:
                cost += e.payload.get("cost_usd", 0.0)
                usage = usage + TokenUsage.model_validate(e.payload.get("usage") or {})
        sub_cost, sub_usage = worker_spend(events, child.run_id)
        cost, usage = cost + sub_cost, usage + sub_usage
    return cost, usage


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
        tracer=_tracing.tracer,
    )


async def _investigate(
    case: Case, loop: AgentLoop, mode: Mode, retrieval: Retrieval = Retrieval.NONE
) -> RunResult[Diagnosis]:
    if not supports(loop.provider.name, mode):
        raise ValueError(UNSUPPORTED.format(provider=loop.provider.name))
    scenario = case.scenario
    server = build_telemetry_server(case.store, make_retriever(retrieval))
    async with mcp_tools(server) as tools:
        if mode is Mode.SINGLE:
            spec, agent_tools = (
                investigator_spec(retrieval=retrieval != Retrieval.NONE),
                list(tools),
            )
        else:
            dispatcher = DispatchWorkers(loop, tools, scenario_id=scenario.id)
            spec, agent_tools = orchestrator_spec(), [*tools, dispatcher]
        return await loop.run(spec, scenario.alert.render(), agent_tools, scenario_id=scenario.id)


async def _run(
    case: Case,
    engine: Engine,
    events: EventLog,
    budget_usd: float,
    mode: Mode,
    approve: bool,
    retrieval: Retrieval = Retrieval.NONE,
) -> RunResult[Diagnosis]:
    if isinstance(engine, ClaudeCodeHarness):
        if not supports(engine.name, mode):
            raise ValueError(UNSUPPORTED.format(provider=engine.name))
        return await engine.investigate(
            case,
            events=events,
            approve=approve,
            budget_usd=budget_usd,
            retrieval=retrieval,
            corpus=CORPUS,
        )
    loop = _loop(engine, events, budget_usd, approve)
    return await _investigate(case, loop, mode, retrieval)


async def investigate(
    case: Case,
    provider: Engine,
    *,
    events: EventLog,
    budget_usd: float,
    mode: Mode = Mode.SINGLE,
    approve: bool | None = None,
    retrieval: Retrieval = Retrieval.NONE,
) -> Investigation:
    allow = case.scenario.approval == "approve" if approve is None else approve
    result = await _run(case, provider, events, budget_usd, mode, allow, retrieval)
    graded = grade(
        case,
        result,
        provider=provider.name,
        mode=mode.value,
        tool_calls=count_tool_calls(events, result.run_id),
    )
    if mode is Mode.MULTI:
        cost, usage = worker_spend(events, result.run_id)
        graded = graded.model_copy(
            update={"cost_usd": round(result.cost_usd + cost, 6), "usage": result.usage + usage}
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
    """Finished rows, and errors not since retried successfully. A line cut short by a
    killed process is skipped; its run is simply done again on resume."""

    def load(name: str) -> list[Row]:
        path = out / name
        if not path.exists():
            return []
        loaded = []
        for line in path.read_text().splitlines():
            try:
                loaded.append(Row.model_validate_json(line))
            except ValueError:
                continue
        return loaded

    rows = load(ROWS)
    done = {(r.config, r.scenario_id, r.rep) for r in rows}
    return rows, [e for e in load(ERRORS) if (e.config, e.scenario_id, e.rep) not in done]


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
    counts = {"ran": 0, "skipped": 0, "crashed": 0}

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
                retrieval=config.retrieval,
            )
        row = Row(config=config.name, scenario_id=case.scenario.id, rep=rep, grade=run.grade)
        target = out / (ERRORS if is_error(by_name[row.config], row) else ROWS)
        async with write:
            await asyncio.to_thread(_append, target, row.model_dump_json())
            counts["ran"] += 1

    # One crashing run (an engine that fails to start, a bug) must not take the rest
    # down; it writes no row, so the next call retries it.
    outcomes = await asyncio.gather(*(one(*t) for t in todo), return_exceptions=True)
    counts["crashed"] = sum(isinstance(o, Exception) for o in outcomes)
    return counts


def summarize_sweep(out: Path) -> tuple[SweepSpec, list[ConfigSummary], list[Row]]:
    spec = SweepSpec.model_validate_json((out / SPEC).read_text())
    rows, errors = read_sweep(out)
    summaries = [
        summarize(
            c,
            [r for r in rows if r.config == c.name],
            [r for r in errors if r.config == c.name],
            k=spec.repeats,
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


QueryKind = Literal["alert", "alert+log"]


def retrieval_queries(scenarios: Path, kind: QueryKind) -> dict[str, str]:
    """What an investigator could search with: the alert alone, or the alert plus the
    first error line from the paging service (one look at the logs)."""
    queries = {}
    for path in discover(scenarios):
        case = load_case(path)
        alert = case.scenario.alert
        text = f"{alert.service}: {alert.summary}"
        if kind == "alert+log":
            errors = case.store.query_logs(alert.service, "error", None, None, None, 1)
            text += " " + (errors[0].message if errors else "")
        queries[case.scenario.id] = text
    return queries


def retrieval_eval(
    mode: Retrieval, scenarios: Path, corpus: Path = CORPUS, query: QueryKind = "alert"
) -> RetrievalReport:
    started = time.perf_counter()
    retriever = make_retriever(mode, corpus)
    index_ms = (time.perf_counter() - started) * 1000
    if retriever is None:
        raise ValueError("retrieval mode 'none' has nothing to evaluate")
    qrels = load_qrels(corpus)
    return evaluate(retriever, retrieval_queries(scenarios, query), qrels, index_ms=index_ms)
