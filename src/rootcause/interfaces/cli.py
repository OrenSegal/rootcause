"""Command line entry points. Built for CI: JSON on request, meaningful exit codes."""

import asyncio
import os
from pathlib import Path
from typing import Annotated

import typer

from rootcause.application.evals import comparison_markdown
from rootcause.composition import (
    CORPUS,
    EffortLevel,
    Mode,
    ProviderName,
    QueryKind,
    Retrieval,
    TraceExporter,
    compare,
    configure_tracing,
    fetch_ci_case,
    investigate,
    load_case,
    load_ci_raw,
    load_sweep_spec,
    open_events,
    plan_sweep,
    provider_factory,
    retrieval_eval,
    run_suite,
    run_sweep,
    telemetry_server,
    triage_ci,
    write_sweep_report,
)
from rootcause.domain.answers import Diagnosis, Outcome

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main(
    trace: Annotated[
        TraceExporter,
        typer.Option(help="Trace agent runs with OpenTelemetry (needs the otel extra)."),
    ] = TraceExporter.NONE,
) -> None:
    """Incident and CI root-cause agent with known-answer evals."""
    configure_tracing(trace)


EXIT_CODES: dict[Outcome, int] = {
    Outcome.ANSWERED: 0,
    Outcome.ERROR: 1,
    Outcome.NO_ANSWER: 2,
    Outcome.STEP_LIMIT: 2,
    Outcome.BUDGET_EXCEEDED: 3,
    Outcome.REFUSED: 4,
}
EVAL_BELOW_THRESHOLD = 1

Provider = Annotated[
    ProviderName,
    typer.Option(help="heuristic needs no API key; claude-code uses the Claude Code sign-in."),
]
Model = Annotated[str, typer.Option(help="Model id for anthropic and claude-code.")]
Effort = Annotated[
    EffortLevel, typer.Option(help="Reasoning effort for anthropic and claude-code.")
]
BudgetUsd = Annotated[float, typer.Option(help="Hard cap in USD per investigation.")]
EventsPath = Annotated[Path, typer.Option("--runlog", help="SQLite event log path.")]
ScenariosDir = Annotated[Path, typer.Argument(help="Directory of scenario directories.")]
RetrievalOpt = Annotated[
    Retrieval,
    typer.Option(help="Postmortem search for the investigator. Dense modes need the rag extra."),
]
CorpusOpt = Annotated[Path, typer.Option(help="Postmortem corpus directory.")]
ModeOpt = Annotated[Mode, typer.Option(help="single agent, or multi (orchestrator plus workers).")]

DEFAULT_RUNLOG = Path(".rootcause/runs.sqlite")


@app.command("investigate")
def investigate_cmd(
    scenario: Annotated[Path, typer.Argument(help="Scenario directory.")],
    provider: Provider = ProviderName.HEURISTIC,
    model: Model = "claude-opus-5-5",
    effort: Effort = EffortLevel.HIGH,
    budget_usd: BudgetUsd = 1.0,
    approve: Annotated[
        bool | None, typer.Option("--approve/--deny", help="Override the scenario's operator.")
    ] = None,
    mode: ModeOpt = Mode.SINGLE,
    runlog: EventsPath = DEFAULT_RUNLOG,
    json_out: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Investigate one scenario. Exit codes: 0 answered, 1 error, 2 no answer or step
    limit, 3 budget exceeded, 4 refused."""
    run = asyncio.run(
        investigate(
            load_case(scenario),
            provider_factory(provider, model, effort)(),
            events=open_events(runlog),
            budget_usd=budget_usd,
            mode=mode,
            approve=approve,
        )
    )
    result, graded = run.result, run.grade
    if json_out:
        typer.echo(run.model_dump_json(indent=2))
    else:
        typer.echo(f"run {result.run_id}: {result.outcome} in {result.steps} step(s)")
        typer.echo(f"  cost ${result.cost_usd:.4f}, {result.wall_ms:.0f} ms")
        if d := result.answer:
            typer.echo(f"  {d.category} in {d.service} (confidence {d.confidence:.2f})")
            typer.echo(f"  {d.summary}")
            typer.echo(f"  evidence: {', '.join(d.evidence)}")
            typer.echo(f"  action: {d.recommended_action}")
        typer.echo(f"  grade: {'pass' if graded.passed else 'fail'}")
    raise typer.Exit(EXIT_CODES[result.outcome])


@app.command("eval")
def eval_cmd(
    scenarios: ScenariosDir = Path("scenarios"),
    provider: Provider = ProviderName.HEURISTIC,
    model: Model = "claude-opus-5-5",
    effort: Effort = EffortLevel.HIGH,
    budget_usd: BudgetUsd = 1.0,
    min_pass_rate: Annotated[float, typer.Option(help="Fail below this pass rate.")] = 1.0,
    mode: ModeOpt = Mode.SINGLE,
    runlog: EventsPath = DEFAULT_RUNLOG,
    json_path: Annotated[Path | None, typer.Option("--json-out", help="Write JSON.")] = None,
    summary_md: Annotated[
        Path | None, typer.Option(help="Append a markdown table (default $GITHUB_STEP_SUMMARY).")
    ] = None,
) -> None:
    """Run every scenario, grade it, and exit 1 below --min-pass-rate."""
    report = asyncio.run(
        run_suite(
            scenarios,
            provider_factory(provider, model, effort),
            events=open_events(runlog),
            budget_usd=budget_usd,
            mode=mode,
        )
    )
    md = report.markdown()
    typer.echo(md)
    if json_path:
        json_path.write_text(report.model_dump_json(indent=2))
    target = summary_md or (Path(p) if (p := os.environ.get("GITHUB_STEP_SUMMARY")) else None)
    if target:
        with target.open("a") as f:
            f.write(md)
    raise typer.Exit(0 if report.pass_rate >= min_pass_rate else EVAL_BELOW_THRESHOLD)


@app.command("compare")
def compare_cmd(
    scenarios: ScenariosDir = Path("scenarios"),
    provider: Annotated[
        list[ProviderName] | None, typer.Option(help="Repeat for several. Default: heuristic.")
    ] = None,
    mode: Annotated[
        list[Mode] | None, typer.Option(help="Repeat for several. Default: both.")
    ] = None,
    model: Model = "claude-opus-5-5",
    effort: Effort = EffortLevel.HIGH,
    budget_usd: BudgetUsd = 1.0,
    runlog: EventsPath = DEFAULT_RUNLOG,
) -> None:
    """Run the same scenarios under each provider and mode and print one table."""
    reports, notes = asyncio.run(
        compare(
            scenarios,
            [provider_factory(p, model, effort) for p in provider or [ProviderName.HEURISTIC]],
            mode or list(Mode),
            events=open_events(runlog),
            budget_usd=budget_usd,
        )
    )
    typer.echo(comparison_markdown(reports, notes))


@app.command("sweep")
def sweep_cmd(
    spec: Annotated[Path, typer.Argument(help="Sweep file (YAML).")],
    out: Annotated[
        Path | None, typer.Option(help="Results directory. Default: results/sweeps/<name>.")
    ] = None,
    concurrency: Annotated[int, typer.Option(help="Investigations in flight at once.")] = 1,
    dry_run: Annotated[bool, typer.Option(help="Print the run count and stop.")] = False,
) -> None:
    """Run every configuration in a sweep file several times. Resumable: runs already
    in the results directory are skipped, and harness errors are retried."""
    loaded = load_sweep_spec(spec)
    target = out or Path("results/sweeps") / loaded.name
    total, done = plan_sweep(loaded, target)
    typer.echo(f"{total} runs planned, {done} done, {total - done} to run")
    if dry_run:
        return
    counts = asyncio.run(run_sweep(loaded, target, concurrency=concurrency))
    typer.echo(f"ran {counts['ran']}, skipped {counts['skipped']}; results in {target}")


@app.command("report")
def report_cmd(
    out: Annotated[Path, typer.Argument(help="A sweep results directory.")],
) -> None:
    """Summarize a sweep: intervals, pass^k, cost per solved incident, frontier.
    Writes report.md and summary.json next to the rows."""
    typer.echo(write_sweep_report(out))


@app.command("ci-triage")
def ci_triage_cmd(
    repo: Annotated[str | None, typer.Option(help="owner/name, with --run.")] = None,
    run: Annotated[int | None, typer.Option(help="GitHub Actions run id.")] = None,
    raw: Annotated[Path | None, typer.Option(help="A recorded raw/ directory, offline.")] = None,
    record: Annotated[Path | None, typer.Option(help="Save the run as a scenario here.")] = None,
    provider: Provider = ProviderName.HEURISTIC,
    mode: ModeOpt = Mode.SINGLE,
    model: Model = "claude-opus-5-5",
    effort: Effort = EffortLevel.HIGH,
    budget_usd: BudgetUsd = 1.0,
    runlog: EventsPath = DEFAULT_RUNLOG,
    comment_md: Annotated[
        Path | None, typer.Option(help="Write a PR comment body here. Nothing is posted.")
    ] = None,
) -> None:
    """Investigate a failed GitHub Actions run. Read-only: uses `gh` to fetch, never posts."""
    if raw is not None:
        case = load_ci_raw(raw)
    elif repo and run:
        case = fetch_ci_case(repo, run, record_to=record)
    else:
        raise typer.BadParameter("pass --repo and --run, or --raw")
    llm = provider_factory(provider, model, effort)()
    result = asyncio.run(
        triage_ci(case, llm, events=open_events(runlog), budget_usd=budget_usd, mode=mode)
    )
    body = ci_comment(result.answer, provider=llm.name, outcome=result.outcome)
    typer.echo(body)
    if comment_md:
        comment_md.write_text(body)
    raise typer.Exit(EXIT_CODES[result.outcome])


def ci_comment(d: Diagnosis | None, *, provider: str, outcome: Outcome) -> str:
    lines = ["### rootcause: likely cause of this CI failure", ""]
    if d is None:
        lines.append(f"No diagnosis ({outcome}).")
    else:
        lines += [
            f"**{d.category}** in `{d.service}` (confidence {d.confidence:.2f})",
            "",
            d.summary,
            "",
            f"Evidence: {', '.join(f'`{e}`' for e in d.evidence)}",
            "",
            f"Suggested next step: {d.recommended_action}",
        ]
    lines += ["", f"<sub>Investigated by {provider}. Review before acting.</sub>"]
    return "\n".join(lines) + "\n"


@app.command("mcp")
def mcp_cmd(
    scenarios: Annotated[Path, typer.Option(help="Directory of scenarios.")] = Path("scenarios"),
    provider: Provider = ProviderName.HEURISTIC,
    model: Model = "claude-opus-5-5",
    effort: Effort = EffortLevel.HIGH,
    budget_usd: BudgetUsd = 1.0,
    runlog: EventsPath = DEFAULT_RUNLOG,
) -> None:
    """Serve rootcause to coding agents over MCP (stdio)."""
    from rootcause.interfaces.mcp_server import build_agent_server

    build_agent_server(
        scenarios.resolve(),
        provider_factory(provider, model, effort),
        open_events(runlog),
        budget_usd,
    ).run()


@app.command("telemetry-mcp")
def telemetry_mcp_cmd(
    scenario: Annotated[Path, typer.Argument(help="Scenario directory.")],
    retrieval: RetrievalOpt = Retrieval.NONE,
    corpus: CorpusOpt = CORPUS,
) -> None:
    """Serve one scenario's raw telemetry tools over MCP (stdio), so any agent,
    Claude Code included, can investigate it directly."""
    telemetry_server(scenario, retrieval, corpus).run()


@app.command("retrieval-eval")
def retrieval_eval_cmd(
    retrieval: Annotated[
        list[Retrieval] | None, typer.Option(help="Repeat for several. Default: bm25.")
    ] = None,
    scenarios: ScenariosDir = Path("scenarios"),
    corpus: CorpusOpt = CORPUS,
    query: Annotated[
        QueryKind, typer.Option(help="alert, or alert plus the first error log line.")
    ] = "alert",
) -> None:
    """Score postmortem search on its own: recall@k, MRR and nDCG@5 against the labels
    in corpus/qrels.yaml, with index time and per-query latency."""
    lines = [
        "| retriever | queries | recall@1 | recall@3 | recall@5 | MRR | nDCG@5 "
        "| index | p50 / p90 query | misses at 5 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for mode in retrieval or [Retrieval.BM25]:
        r = retrieval_eval(mode, scenarios, corpus, query)
        lines.append(
            f"| {r.retriever} | {r.queries} | {r.recall_at_1:.2f} | {r.recall_at_3:.2f} "
            f"| {r.recall_at_5:.2f} | {r.mrr:.2f} | {r.ndcg_at_5:.2f} | {r.index_ms:.0f} ms "
            f"| {r.p50_query_ms:.1f} / {r.p90_query_ms:.1f} ms | {len(r.misses)} |"
        )
    typer.echo("\n".join(lines))
