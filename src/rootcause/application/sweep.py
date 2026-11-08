"""Sweeps: the same scenarios under several configurations, each run several times.

A single pass over the suite gives one pass rate with no error bar. A sweep
repeats every (configuration, scenario) pair and summarizes each configuration
with what a cost/accuracy decision needs: pass rate with an interval that resamples
scenarios (repeats of one scenario are correlated, so runs are not independent),
pass^k (every attempt right) and pass@k (any attempt right), cost per solved
incident, and latency percentiles. A configuration is dominated when another
one is at least as reliable (pass^k) for no more money and better on one of
the two.

Harness errors are kept apart from model failures throughout: they are not in
the pass rate, but their cost is in cost per solved incident, because it was
spent. A run served by a different model than the one configured is an error,
since its grade says nothing about the configured model.
"""

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from enum import StrEnum
from html import escape
from typing import Literal

from pydantic import BaseModel, model_validator

from rootcause.application.evals import Grade
from rootcause.application.investigator import SEARCH_POSTMORTEMS
from rootcause.application.stats import cluster_bootstrap, pass_at_k, pass_hat_k, percentile


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
    brief: str | None = None  # path to a file replacing the investigator's instructions

    @property
    def checks_served_model(self) -> bool:
        return self.provider != "heuristic"


class SweepSpec(BaseModel):
    name: str
    repeats: int = 3
    scenarios: str = "scenarios"
    scenario_ids: list[str] = []  # run only these; empty means every scenario in the directory
    # Scenarios whose results are reported apart and never used to change the brief, the
    # grader or an accepted set. The split is a check on tuning, not on authorship: every
    # scenario here has the same author.
    held_out: list[str] = []
    configs: list[SweepConfig]

    @model_validator(mode="after")
    def _unique_names(self) -> SweepSpec:
        names = [c.name for c in self.configs]
        if dupes := sorted({n for n in names if names.count(n) > 1}):
            raise ValueError(f"duplicate config names: {', '.join(dupes)}")
        return self

    def selects(self, scenario_id: str) -> bool:
        return not self.scenario_ids or scenario_id in self.scenario_ids


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
    config: SweepConfig,
    rows: Sequence[Row],
    errors: Sequence[Row] = (),
    *,
    k: int | None = None,
    strict_service: bool = False,
) -> ConfigSummary:
    """`k` should be the same for every configuration being compared (the sweep's
    repeat count): a configuration that lost runs to errors must not get a smaller k
    and with it an easier pass^k. A scenario with fewer than k scored runs counts as
    not shown reliable for pass^k, and as passed for pass@k only if a run passed."""
    scored = [r for r in rows if not is_error(config, r)]
    failed = [*errors, *(r for r in rows if is_error(config, r))]
    passes = sum(r.grade.passes(strict_service=strict_service) for r in scored)
    by_scenario: dict[str, list[bool]] = defaultdict(list)
    for r in scored:
        by_scenario[r.scenario_id].append(r.grade.passes(strict_service=strict_service))
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
    lo, hi = cluster_bootstrap([by_scenario[s] for s in sorted(by_scenario)])
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
    spec: SweepSpec,
    summaries: Sequence[ConfigSummary],
    rows: Sequence[Row],
    *,
    strict_service: bool = False,
) -> str:
    k = min((s.k for s in summaries), default=1)
    graded = "single-service grade" if strict_service else "accepted-service grade"
    lines = [
        f"### Sweep `{spec.name}`: {len(summaries)} configurations, {spec.repeats} runs each "
        f"({graded})",
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
    if spec.held_out:
        lines += ["", "| config | in-sample pass rate (95% CI) | held-out pass rate (95% CI) |",
                  "|---|---|---|"]  # fmt: skip
        for c in spec.configs:
            cells = []
            for want in (False, True):
                part = [r for r in rows if r.config == c.name
                        and (r.scenario_id in spec.held_out) is want]  # fmt: skip
                p = summarize(c, part, k=spec.repeats, strict_service=strict_service)
                cells.append(
                    f"{_pct(p.pass_rate)} ({_pct(p.ci_low)} to {_pct(p.ci_high)}), {p.scored} runs"
                    if p.scored
                    else "no runs"
                )
            lines.append(f"| {c.name} | {cells[0]} | {cells[1]} |")
        lines += ["", "Held out: " + ", ".join(spec.held_out) + ". Results on these are never "
                  "used to change the brief, the grader or an accepted set."]  # fmt: skip
    names = [s.name for s in summaries]
    grid: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        grid[r.scenario_id][r.config].append(r.grade.passes(strict_service=strict_service))
    lines += ["", "| scenario | " + " | ".join(names) + " |", "|---" * (len(names) + 1) + "|"]
    for sid in sorted(grid):
        cells = [f"{sum(grid[sid][n])}/{len(grid[sid][n])}" for n in names]
        lines.append(f"| {sid} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def report_svg(spec: SweepSpec, summaries: Sequence[ConfigSummary]) -> str:
    """Cost per solved incident (log x) against pass rate with its interval from
    resampling scenarios (y), one labelled point per configuration. Hollow points are
    dominated. A configuration that solved nothing, or cost nothing, has no point on a
    log scale and is listed under the chart."""
    plotted = [(s, s.cost_per_pass) for s in summaries if s.cost_per_pass]
    missing = [escape(s.name) for s in summaries if not s.cost_per_pass]
    left, top, pw, ph = 60, 50, 440, 300
    width, height = left + pw + 280, top + ph + 70 + 16 * bool(missing)
    xs = [math.log10(c) for _, c in plotted] or [0.0]
    x0, x1 = min(xs) - 0.15, max(xs) + 0.15
    y0 = math.floor(min((s.ci_low for s, _ in plotted), default=0.0) * 20) / 20
    y1 = math.ceil(max((s.ci_high for s, _ in plotted), default=1.0) * 20) / 20
    y1 = max(y1, y0 + 0.05)

    def px(cost: float) -> float:
        return left + (math.log10(cost) - x0) / (x1 - x0) * pw

    def py(rate: float) -> float:
        return top + (y1 - rate) / (y1 - y0) * ph

    face = 'font-family="Helvetica, Arial, sans-serif" fill="#222"'
    font = f'{face} font-size="12"'
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">',
        f"<title>Sweep {escape(spec.name)}: cost per solved incident vs pass rate</title>",
        f'<rect width="{width}" height="{height}" fill="#fff"/>',
        f'<text x="{left}" y="24" {face} font-size="14" font-weight="bold">'
        f"{escape(spec.name)}: pass rate (95% CI, scenarios resampled) "
        "vs cost per solved incident</text>",
        f'<rect x="{left}" y="{top}" width="{pw}" height="{ph}" fill="none" stroke="#999"/>',
    ]
    tick = y0
    while tick <= y1 + 1e-9:
        y = py(tick)
        out += [
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + pw}" y2="{y:.1f}" stroke="#eee"/>',
            f'<text x="{left - 6}" y="{y + 4:.1f}" {font} text-anchor="end">{tick:.0%}</text>',
        ]
        tick += 0.05
    decades = range(math.floor(x0), math.ceil(x1) + 1)
    ticks = [m * 10.0**d for d in decades for m in (1, 2, 5)]
    if sum(x0 <= math.log10(c) <= x1 for c in ticks) < 3:  # narrow range: every digit
        ticks = [m * 10.0**d for d in decades for m in range(1, 10)]
    for cost in ticks:
        if x0 <= math.log10(cost) <= x1:
            x = px(cost)
            out += [
                f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + ph}" stroke="#eee"/>',
                f'<text x="{x:.1f}" y="{top + ph + 16}" {font} text-anchor="middle">'
                f"${cost:g}</text>",
            ]
    # Labels sit in a column right of the plot, spread so they never overlap, with a
    # leader line to their point: nearby configurations are the common case.
    placed = sorted(((py(s.pass_rate), s, cost) for s, cost in plotted), key=lambda p: p[0])
    label_y: list[float] = []
    for y, _, _ in placed:
        label_y.append(max(y, label_y[-1] + 15) if label_y else y)
    shift = max(0.0, (label_y[-1] if label_y else 0) - (top + ph))
    label_y = [ly - shift for ly in label_y]
    lx = left + pw + 12
    for (y, s, cost), ly in zip(placed, label_y, strict=True):
        name, x = escape(s.name), px(cost)
        fill = "#fff" if s.dominated else "#1f5fa8"
        out += [
            f'<g data-config="{name}">',
            f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{lx - 2}" y2="{ly:.1f}" stroke="#ccc"/>',
            f'<line x1="{x:.1f}" y1="{py(s.ci_low):.1f}" x2="{x:.1f}" y2="{py(s.ci_high):.1f}" '
            'stroke="#1f5fa8" stroke-width="1.5"/>',
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{fill}" stroke="#1f5fa8" '
            'stroke-width="1.5"/>',
            f'<text x="{lx}" y="{ly + 4:.1f}" {font}>{name} '
            f"{_pct(s.pass_rate)}, ${cost:.4f}</text>",
            "</g>",
        ]
    foot = top + ph + 38
    out.append(
        f'<text x="{left}" y="{foot}" {font}>cost per solved incident (log scale, includes '
        "spend on failed runs). Filled: on the frontier. Hollow: dominated.</text>"
    )
    if missing:
        out.append(
            f'<text x="{left}" y="{foot + 16}" {font}>'
            "Not plotted, no cost per solved incident to place on a log scale: "
            f"{', '.join(missing)}</text>"
        )
    return "\n".join(out) + "\n</svg>\n"
