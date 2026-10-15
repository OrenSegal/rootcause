"""Sweeps: the same scenarios under several configurations, each run several times.

A single pass over the suite gives one pass rate with no error bar. A sweep
repeats every (configuration, scenario) pair and summarizes each configuration
with what a cost/accuracy decision needs: pass rate with a Wilson interval,
pass^k (every attempt right) and pass@k (any attempt right), cost per solved
incident, and latency percentiles. A configuration is dominated when another
one is at least as reliable (pass^k) for no more money and better on one of
the two.

Harness errors are kept apart from model failures throughout: they are not in
the pass rate, but their cost is in cost per solved incident, because it was
spent. A run served by a different model than the one configured is an error,
since its grade says nothing about the configured model.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, model_validator

from rootcause.application.evals import Grade
from rootcause.application.investigator import SEARCH_POSTMORTEMS
from rootcause.application.stats import pass_at_k, pass_hat_k, percentile, wilson


class Retrieval(StrEnum):
    """Which postmortem search the investigator gets, cheapest first."""

    NONE = "none"
    BM25 = "bm25"
    STATIC = "static"  # static embeddings (model2vec)
    DENSE = "dense"  # transformer embeddings (fastembed)
    HYBRID = "hybrid"  # BM25 and dense, rank-fused


class SweepConfig(BaseModel):
    """One configuration. Provider and effort names are checked where engines are
    built (composition), which is the one place that knows the valid values."""

    name: str
    provider: str
    model: str = "claude-opus-5-5"
    effort: str = "high"
    mode: Literal["single", "multi"] = "single"
    retrieval: Retrieval = Retrieval.NONE
    budget_usd: float = 1.0

    @property
    def checks_served_model(self) -> bool:
        return self.provider != "heuristic"


class SweepSpec(BaseModel):
    name: str
    repeats: int = 3
    scenarios: str = "scenarios"
    configs: list[SweepConfig]

    @model_validator(mode="after")
    def _unique_names(self) -> SweepSpec:
        names = [c.name for c in self.configs]
        if dupes := sorted({n for n in names if names.count(n) > 1}):
            raise ValueError(f"duplicate config names: {', '.join(dupes)}")
        return self


class Row(BaseModel):
    config: str
    scenario_id: str
    rep: int
    grade: Grade


class ConfigSummary(BaseModel):
    name: str
    runs: int
    scored: int
    errors: int
    passes: int
    pass_rate: float
    ci_low: float
    ci_high: float
    k: int
    pass_hat_k: float
    pass_at_k: float
    mean_cost: float
    cost_per_pass: float | None
    p50_wall_s: float
    p90_wall_s: float
    mean_output_tokens: float
    mean_tool_calls: float
    search_rate: float = 0.0  # share of scored runs that called search_postmortems
    short_scenarios: int = 0  # scenarios with fewer than k scored runs
    dominated: bool = False


def is_error(config: SweepConfig, row: Row) -> bool:
    g = row.grade
    swapped = config.checks_served_model and bool(g.models) and g.models != [config.model]
    return g.status == "error" or swapped


def summarize(
    config: SweepConfig, rows: Sequence[Row], errors: Sequence[Row] = (), *, k: int | None = None
) -> ConfigSummary:
    """`k` should be the same for every configuration being compared (the sweep's
    repeat count): a configuration that lost runs to errors must not get a smaller k
    and with it an easier pass^k. A scenario with fewer than k scored runs counts as
    not shown reliable for pass^k, and as passed for pass@k only if a run passed."""
    scored = [r for r in rows if not is_error(config, r)]
    failed = [*errors, *(r for r in rows if is_error(config, r))]
    passes = sum(r.grade.passed for r in scored)
    by_scenario: dict[str, list[bool]] = defaultdict(list)
    for r in scored:
        by_scenario[r.scenario_id].append(r.grade.passed)
    k = k or min((len(v) for v in by_scenario.values()), default=1)
    hat = [
        pass_hat_k(n=len(v), c=sum(v), k=k) if len(v) >= k else 0.0 for v in by_scenario.values()
    ]
    at = [
        pass_at_k(n=len(v), c=sum(v), k=k) if len(v) >= k else float(any(v))
        for v in by_scenario.values()
    ]
    short = sum(len(v) < k for v in by_scenario.values())
    spent_scored = sum(r.grade.cost_usd for r in scored)
    spent = spent_scored + sum(r.grade.cost_usd for r in failed)
    walls = [r.grade.wall_ms / 1000 for r in scored]
    lo, hi = wilson(passes, len(scored))
    n = len(scored) or 1
    return ConfigSummary(
        name=config.name,
        runs=len(scored) + len(failed),
        scored=len(scored),
        errors=len(failed),
        passes=passes,
        pass_rate=passes / n,
        ci_low=lo,
        ci_high=hi,
        k=k,
        pass_hat_k=sum(hat) / len(hat) if hat else 0.0,
        pass_at_k=sum(at) / len(at) if at else 0.0,
        mean_cost=spent_scored / n,
        short_scenarios=short,
        cost_per_pass=spent / passes if passes else None,
        p50_wall_s=percentile(walls, 50),
        p90_wall_s=percentile(walls, 90),
        mean_output_tokens=sum(r.grade.usage.output_tokens for r in scored) / n,
        mean_tool_calls=sum(r.grade.tool_calls for r in scored) / n,
        search_rate=sum(SEARCH_POSTMORTEMS in r.grade.tools_used for r in scored) / n,
    )


def frontier(summaries: Iterable[ConfigSummary]) -> list[ConfigSummary]:
    """Mark each configuration that another one beats on reliability for no more cost."""
    items = list(summaries)

    def beats(a: ConfigSummary, b: ConfigSummary) -> bool:
        no_worse = a.pass_hat_k >= b.pass_hat_k and a.mean_cost <= b.mean_cost
        better = a.pass_hat_k > b.pass_hat_k or a.mean_cost < b.mean_cost
        return no_worse and better

    return [
        s.model_copy(update={"dominated": any(beats(o, s) for o in items if o is not s)})
        for s in items
    ]


def _pct(x: float) -> str:
    return f"{x:.0%}"


def report_markdown(
    spec: SweepSpec, summaries: Sequence[ConfigSummary], rows: Sequence[Row]
) -> str:
    k = min((s.k for s in summaries), default=1)
    lines = [
        f"### Sweep `{spec.name}`: {len(summaries)} configurations, {spec.repeats} runs each",
        "",
        f"| config | scored / errors | pass rate (95% CI) | pass^{k} | pass@{k} | mean cost "
        "| cost per solved | p50 / p90 wall | output tokens | tool calls | searched | frontier |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        per_pass = f"${s.cost_per_pass:.4f}" if s.cost_per_pass is not None else "n/a"
        lines.append(
            f"| {s.name} | {s.scored} / {s.errors} "
            f"| {_pct(s.pass_rate)} ({_pct(s.ci_low)} to {_pct(s.ci_high)}) "
            f"| {_pct(s.pass_hat_k)} | {_pct(s.pass_at_k)} | ${s.mean_cost:.4f} | {per_pass} "
            f"| {s.p50_wall_s:.1f}s / {s.p90_wall_s:.1f}s | {s.mean_output_tokens:.0f} "
            f"| {s.mean_tool_calls:.1f} | {_pct(s.search_rate)} "
            f"| {'dominated' if s.dominated else 'yes'} |"
        )
    if short := [s for s in summaries if s.short_scenarios]:
        lines += [
            "",
            "Scenarios with fewer than k scored runs, counted as not shown reliable in pass^k: "
            + ", ".join(f"{s.name} {s.short_scenarios}" for s in short)
            + ".",
        ]
    names = [s.name for s in summaries]
    grid: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        grid[r.scenario_id][r.config].append(r.grade.passed)
    lines += ["", "| scenario | " + " | ".join(names) + " |", "|---" * (len(names) + 1) + "|"]
    for sid in sorted(grid):
        cells = [f"{sum(grid[sid][n])}/{len(grid[sid][n])}" for n in names]
        lines.append(f"| {sid} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
