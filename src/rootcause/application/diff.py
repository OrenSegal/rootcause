"""Did B beat A, or is the gap noise? A paired comparison of two result sets.

The verdict rule: B is better when the paired 95% interval for B minus A lies above
zero and the sign test gives p < 0.05; A is better when the interval lies below zero
and p < 0.05; otherwise there is no detectable difference. Both must agree because
with few differing cases the bootstrap interval can exclude zero while the sign test,
which only counts cases, cannot.
"""

from collections import defaultdict
from collections.abc import Sequence
from statistics import stdev
from typing import Literal

from pydantic import BaseModel

from rootcause.application.stats import (
    Runs,
    cluster_bootstrap,
    diffs,
    mde80,
    n_for_mde,
    paired_gap,
    rate,
    sign_test,
)

Verdict = Literal["b_better", "a_better", "no_difference"]


class RunRecord(BaseModel):
    """One run of one case. Harness errors count toward spend, not the pass rate."""

    case: str
    passed: bool
    cost: float | None = None
    error: bool = False


class SideSummary(BaseModel):
    label: str
    runs: int
    pass_rate: float
    ci_low: float
    ci_high: float
    cost_per_solved: float | None


class DiffReport(BaseModel):
    a: SideSummary
    b: SideSummary
    cases: int
    dropped: int
    gap: float  # B minus A, points
    gap_low: float
    gap_high: float
    b_wins: int
    a_wins: int
    sign_p: float
    mde80: float | None  # None when no case differs
    cases_for_gap: int | None  # cases an MDE80 equal to the observed gap would need
    verdict: Verdict


def _side(label: str, records: Sequence[RunRecord], ids: list[str], runs: Runs) -> SideSummary:
    wanted = set(ids)
    kept = [r for r in records if r.case in wanted]
    lo, hi = cluster_bootstrap([runs[s] for s in ids])
    passes = sum(r.passed for r in kept if not r.error)
    priced = any(r.cost is not None for r in kept)
    spent = sum(r.cost or 0.0 for r in kept)
    return SideSummary(
        label=label,
        runs=sum(len(runs[s]) for s in ids),
        pass_rate=rate(runs, ids),
        ci_low=lo,
        ci_high=hi,
        cost_per_solved=spent / passes if priced and passes else None,
    )


def _runs(records: Sequence[RunRecord]) -> Runs:
    out: Runs = defaultdict(list)
    for r in records:
        if not r.error:
            out[r.case].append(r.passed)
    return dict(out)


def compare(
    a: tuple[str, Sequence[RunRecord]],
    b: tuple[str, Sequence[RunRecord]],
    *,
    intersect: bool = False,
) -> DiffReport:
    """Paired comparison over the cases both sides scored. Cases on one side only are an
    error unless `intersect`, which drops them and counts them in `dropped`."""
    ra, rb = _runs(a[1]), _runs(b[1])
    if (only := sorted(set(ra) ^ set(rb))) and not intersect:
        raise ValueError(
            f"cases on one side only: {', '.join(only)}. Pass --intersect to drop them."
        )
    ids = sorted(set(ra) & set(rb))
    if len(ids) < 2:
        raise ValueError(f"need at least 2 cases scored on both sides, got {len(ids)}")
    ra, rb = {s: ra[s] for s in ids}, {s: rb[s] for s in ids}
    gap, lo, hi = paired_gap(rb, ra)
    d = diffs(rb, ra)
    b_wins, a_wins, p = sign_test(d)
    sd = stdev(d)
    verdict: Verdict = "no_difference"
    if p < 0.05 and lo > 0:
        verdict = "b_better"
    elif p < 0.05 and hi < 0:
        verdict = "a_better"
    return DiffReport(
        a=_side(a[0], a[1], ids, ra),
        b=_side(b[0], b[1], ids, rb),
        cases=len(ids),
        dropped=len(only),
        gap=gap,
        gap_low=lo,
        gap_high=hi,
        b_wins=b_wins,
        a_wins=a_wins,
        sign_p=p,
        mde80=mde80(d) if sd else None,
        cases_for_gap=n_for_mde(d, abs(gap) / 100) if sd and gap else None,
        verdict=verdict,
    )


def diff_markdown(r: DiffReport) -> str:
    def cost(s: SideSummary) -> str:
        return f"${s.cost_per_solved:.4f}" if s.cost_per_solved is not None else "n/a"

    def pct(x: float) -> str:
        return f"{100 * x:.1f}%"

    lines = [
        f"### B minus A over {r.cases} cases",
        "",
        "| side | results | scored runs | pass rate (95% CI, cases resampled) | cost per solved |",
        "|---|---|---|---|---|",
    ]
    for name, s in (("A", r.a), ("B", r.b)):
        lines.append(
            f"| {name} | {s.label} | {s.runs} | {pct(s.pass_rate)} "
            f"({pct(s.ci_low)} to {pct(s.ci_high)}) | {cost(s)} |"
        )
    ties = r.cases - r.b_wins - r.a_wins
    mde = f"{r.mde80:.1f} points" if r.mde80 is not None else "undefined, no case differs"
    lines += [
        "",
        f"- Paired gap, B minus A: {r.gap:+.1f} points ({r.gap_low:+.1f} to {r.gap_high:+.1f}), "
        "cases resampled with the same draw for both sides.",
        f"- Sign test: B better on {r.b_wins} cases, A better on {r.a_wins}, {ties} tied; "
        f"two-sided p = {r.sign_p:.3f}.",
        f"- Minimum detectable effect at 80% power (paired t, {r.cases} cases): {mde}.",
        "- Cost per solved counts spend on failed runs and harness errors.",
    ]
    if r.dropped:
        lines.append(f"- Dropped {r.dropped} cases present on one side only (--intersect).")
    if r.verdict == "b_better":
        verdict = "B is better."
    elif r.verdict == "a_better":
        verdict = "A is better: B is a regression."
    else:
        verdict = "no detectable difference."
        if r.mde80 is not None:
            verdict += f" This design detects a gap of about {r.mde80:.1f} points."
        if r.cases_for_gap is not None:
            verdict += (
                f" The observed {r.gap:+.1f} would need about {r.cases_for_gap} cases"
                " to be detectable."
            )
    lines += ["", f"**Verdict: {verdict}**"]
    return "\n".join(lines) + "\n"
