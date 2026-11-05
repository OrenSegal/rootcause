"""Reproduce the README's lenient pass rates and resampled gaps from committed sweep rows.

    uv run python scripts/resample_gaps.py

Prints each configuration's pass rate with the service check dropped (models sweep),
then a paired bootstrap over scenarios for two gaps: Opus high minus Sonnet (service
check dropped) and the single-agent loop minus LangGraph (grader as is). Each draw
resamples the 25 scenarios with replacement and keeps all of a scenario's runs, the
same draw for both sides of a gap.
"""

import random
import sys
from collections import defaultdict
from pathlib import Path

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


def self_check() -> None:
    a = {"x": [True, True], "y": [True, False]}
    b = {"x": [False, False], "y": [True, False]}
    point, lo, hi = paired_gap(a, b, draws=2000)
    assert point == 50.0 and 0.0 <= lo <= point <= hi <= 100.0, (point, lo, hi)
    same = paired_gap(a, a, draws=200)
    assert same == (0.0, 0.0, 0.0), same


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
    point, lo, hi = paired_gap(
        by_scenario(loop, "loop-opus-5.5-high-single", passed),
        by_scenario(graph, "langgraph-opus-5.5-high-single", passed),
    )
    print(f"loop minus LangGraph, single agent: {point:+.1f} ({lo:+.1f} to {hi:+.1f})")


if __name__ == "__main__":
    sys.exit(main())
