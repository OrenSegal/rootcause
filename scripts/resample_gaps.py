"""Reproduce the README's lenient pass rates and resampled gaps from committed sweep rows.

    uv run python scripts/resample_gaps.py

Prints each configuration's pass rate with the service check dropped (models sweep),
then a paired bootstrap over scenarios for Opus high minus Sonnet and minus Haiku, with
the service check dropped (a post-hoc grade, not corrected for other pairs). Each draw
resamples the 25 scenarios with replacement and keeps all of a scenario's runs, the
same draw for both sides of a gap.

Next, how much the repeats of one scenario agree, per model-sweep setting: the
intraclass correlation (one-way ANOVA, 3 runs per scenario), the effective number of
independent runs it implies, and how much wider the scenario-resampled interval is than a
Wilson interval over all 75 runs.

Then the same gaps under the strict grader (the grade as recorded) for the comparisons
the README draws: Opus high minus Haiku, loop minus LangGraph, and single agent minus
orchestrator per harness and pooled. For each: the per-scenario differences d_i, their
sd, the minimum detectable effect at 80% power, an exact sign test on the scenarios
that differ, and Holm-adjusted p-values across the pairs printed. Last, every pair of
the seven model-sweep settings (21 comparisons), Holm-adjusted together, since that is
how many the table invites.
"""

import itertools
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from rootcause.application.stats import cluster_bootstrap, wilson
from rootcause.application.sweep import Row
from rootcause.composition import read_sweep

SWEEPS = Path(__file__).resolve().parent.parent / "results" / "sweeps"
DRAWS = 10_000
SEED = 0


def lenient(row: Row) -> bool:
    """Passed, with the service check counted as right."""
    return row.grade.model_copy(update={"service_ok": True}).passed


def by_scenario(rows: list[Row], config: str, ok) -> dict[str, list[bool]]:
    out: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        if r.config == config:
            out[r.scenario_id].append(ok(r))
    return out


def rate(runs: dict[str, list[bool]], ids: list[str]) -> float:
    hits = [x for s in ids for x in runs[s]]
    return sum(hits) / len(hits)


def paired_gap(
    a: dict[str, list[bool]], b: dict[str, list[bool]], draws: int = DRAWS, seed: int = SEED
) -> tuple[float, float, float]:
    """Point gap a - b and its 95% percentile interval, in points."""
    ids = sorted(a)
    assert ids == sorted(b), "both sides need the same scenarios"
    rng = random.Random(seed)
    gaps = []
    for _ in range(draws):
        pick = rng.choices(ids, k=len(ids))
        gaps.append(rate(a, pick) - rate(b, pick))
    gaps.sort()
    lo, hi = gaps[int(0.025 * draws)], gaps[int(0.975 * draws) - 1]
    return 100 * (rate(a, ids) - rate(b, ids)), 100 * lo, 100 * hi


# t quantiles at 24 df (25 scenarios): two-sided 5% and one-sided 20%.
# shortcut: fixed to 25 scenarios, look the quantiles up again if the suite changes size.
T_24 = 2.064 + 0.857


def diffs(a: dict[str, list[bool]], b: dict[str, list[bool]]) -> list[float]:
    """Per-scenario pass-rate difference a - b, the paired unit."""
    assert sorted(a) == sorted(b), "both sides need the same scenarios"
    return [rate(a, [s]) - rate(b, [s]) for s in sorted(a)]


def mde80(d: list[float]) -> float:
    """Smallest true gap (points) a paired t-test at 5% detects 80% of the time.
    Approximate: many d_i are exactly zero, so the d_i are far from normal."""
    assert len(d) == 25, "T_24 assumes 25 scenarios"
    return 100 * T_24 * statistics.stdev(d) / math.sqrt(len(d))


def n_for_mde(d: list[float], gap: float) -> int:
    """Scenarios needed for an MDE80 of `gap` at this sd (normal quantiles, approximate)."""
    return math.ceil(((1.960 + 0.842) * statistics.stdev(d) / gap) ** 2)


def sign_test(d: list[float]) -> tuple[int, int, float]:
    """Exact two-sided sign test on the scenarios that differ: (positive, negative, p)."""
    pos, neg = sum(x > 0 for x in d), sum(x < 0 for x in d)
    n = pos + neg
    tail = sum(math.comb(n, i) for i in range(min(pos, neg) + 1)) / 2**n
    return pos, neg, min(1.0, 2 * tail)


def holm(ps: list[float]) -> list[float]:
    """Holm step-down adjusted p-values, in the input order."""
    order = sorted(range(len(ps)), key=ps.__getitem__)
    out, running = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        running = max(running, (len(ps) - rank) * ps[i])
        out[i] = min(1.0, running)
    return out


def icc(clusters: list[list[bool]]) -> float:
    """One-way ANOVA intraclass correlation for equal-size clusters, clamped at 0."""
    k, m = len(clusters[0]), len(clusters)
    assert all(len(c) == k for c in clusters), "equal runs per scenario"
    means = [sum(c) / k for c in clusters]
    grand = sum(means) / m
    msb = k * sum((x - grand) ** 2 for x in means) / (m - 1)
    msw = sum((x - mu) ** 2 for c, mu in zip(clusters, means, strict=True) for x in c) / (
        m * (k - 1)
    )
    if msb + (k - 1) * msw == 0:
        return 0.0
    return max(0.0, (msb - msw) / (msb + (k - 1) * msw))


def width_ratio(clusters: list[list[bool]]) -> float:
    """Scenario-resampled interval width over the Wilson-over-runs width."""
    runs = [x for c in clusters for x in c]
    assert 0 < sum(runs) < len(runs), "cluster_bootstrap falls back to Wilson at 0% or 100%"
    lo, hi = cluster_bootstrap(clusters)
    wlo, whi = wilson(sum(runs), len(runs))
    return (hi - lo) / (whi - wlo)


def self_check() -> None:
    unanimous = [[True] * 3, [False] * 3] * 2
    assert icc(unanimous) == 1.0
    assert icc([[True, False, True], [False, True, True]]) == 0.0
    a = {"x": [True, True], "y": [True, False]}
    b = {"x": [False, False], "y": [True, False]}
    point, lo, hi = paired_gap(a, b, draws=2000)
    assert point == 50.0 and 0.0 <= lo <= point <= hi <= 100.0, (point, lo, hi)
    same = paired_gap(a, a, draws=200)
    assert same == (0.0, 0.0, 0.0), same
    # 5 of 25 scenarios differ by a full point: sd = sqrt(0.16 * 25 / 24), MDE 23.85 points
    d = [1.0] * 5 + [0.0] * 20
    assert abs(mde80(d) - 23.85) < 0.01, mde80(d)
    assert sign_test(d) == (5, 0, 0.0625)
    assert sign_test([1.0, -1.0, 0.0]) == (1, 1, 1.0)
    assert holm([0.01, 0.04, 0.03]) == [0.03, 0.06, 0.06]


def main() -> None:
    self_check()
    models, _ = read_sweep(SWEEPS / "models-and-retrieval-2026-10-15")
    configs = sorted({r.config for r in models})
    print("models-and-retrieval-2026-10-15, pass rate without the service check:")
    for c in configs:
        runs = by_scenario(models, c, lenient)
        print(f"  {c:24} {100 * rate(runs, sorted(runs)):5.1f}%")

    opus = by_scenario(models, "opus-5.5-high", lenient)
    sonnet = by_scenario(models, "sonnet-5.5-high", lenient)
    haiku = by_scenario(models, "haiku-5.5-high", lenient)
    for name, other in (("Sonnet", sonnet), ("Haiku", haiku)):
        point, lo, hi = paired_gap(opus, other)
        print(f"Opus high minus {name}, no service check: {point:+.1f} ({lo:+.1f} to {hi:+.1f})")

    loop, _ = read_sweep(SWEEPS / "loop-2026-10-15")
    graph, _ = read_sweep(SWEEPS / "langgraph-2026-10-15")
    passed = lambda r: r.grade.passed  # noqa: E731
    strict = {c: by_scenario(models, c, passed) for c in configs}

    print("\nRepeats of one scenario, strict grader (75 runs = 25 scenarios x 3):")
    for c in configs:
        clusters = [strict[c][s] for s in sorted(strict[c])]
        rho = icc(clusters)
        n_runs = sum(map(len, clusters))
        print(
            f"  {c:24} ICC {rho:.2f}, effective n {n_runs / (1 + 2 * rho):4.1f}, "
            f"interval width vs Wilson over runs {width_ratio(clusters):.2f}x"
        )
    single = {
        h: by_scenario(rows, f"{h}-opus-5.5-high-single", passed)
        for h, rows in (("loop", loop), ("langgraph", graph))
    }
    multi = {
        h: by_scenario(rows, f"{h}-opus-5.5-high-multi", passed)
        for h, rows in (("loop", loop), ("langgraph", graph))
    }
    pooled = [{s: x["loop"][s] + x["langgraph"][s] for s in x["loop"]} for x in (single, multi)]
    pairs = {
        "Opus high minus Haiku": (strict["opus-5.5-high"], strict["haiku-5.5-high"]),
        "loop minus LangGraph, single agent": (single["loop"], single["langgraph"]),
        "single minus orchestrator, loop": (single["loop"], multi["loop"]),
        "single minus orchestrator, LangGraph": (single["langgraph"], multi["langgraph"]),
        "single minus orchestrator, pooled": (pooled[0], pooled[1]),
        "hybrid minus BM25 search": (strict["opus-5.5-high-hybrid"], strict["opus-5.5-high-bm25"]),
    }
    print(
        "\nStrict grader, paired by scenario. MDE80 is t-based and approximate (most d_i are 0);"
        f"\nsign test on scenarios that differ; Holm across these {len(pairs)} pairs:"
    )
    tests = {name: sign_test(diffs(a, b)) for name, (a, b) in pairs.items()}
    adjusted = holm([p for _, _, p in tests.values()])
    for (name, (a, b)), (pos, neg, p), p_holm in zip(
        pairs.items(), tests.values(), adjusted, strict=True
    ):
        point, lo, hi = paired_gap(a, b)
        d = diffs(a, b)
        print(
            f"  {name}: {point:+.1f} ({lo:+.1f} to {hi:+.1f}), sd(d_i) "
            f"{100 * statistics.stdev(d):.1f}, MDE80 {mde80(d):.1f}, "
            f"differ {pos}+/{neg}-, sign p {p:.3f}, Holm {p_holm:.3f}, "
            f"scenarios for a 10-point MDE80 ~{n_for_mde(d, 0.10)}"
        )

    every = list(itertools.combinations(configs, 2))
    ps = [sign_test(diffs(strict[a], strict[b]))[2] for a, b in every]
    print(f"\nAll {len(every)} pairs of the models sweep, strict grader, Holm across all of them:")
    for (a, b), p, p_holm in zip(every, ps, holm(ps), strict=True):
        point, lo, hi = paired_gap(strict[a], strict[b])
        excludes = "  interval excludes 0" if lo > 0 or hi < 0 else ""
        print(
            f"  {a} minus {b}: {point:+.1f} ({lo:+.1f} to {hi:+.1f}), "
            f"MDE80 {mde80(diffs(strict[a], strict[b])):.1f}, sign p {p:.3f}, "
            f"Holm {p_holm:.3f}{excludes}"
        )


if __name__ == "__main__":
    sys.exit(main())
