"""Small-sample statistics for eval results.

With a few dozen scenarios, a pass rate on its own hides how wide the
uncertainty is. These are the estimators the reports use:

- Wilson score interval: a binomial confidence interval that stays sensible
  near 0% and 100%, where the normal approximation does not. It treats every
  run as independent, so it is too narrow for repeats of the same scenario.
- Cluster bootstrap: resamples scenarios, keeping all of a scenario's runs, so
  the interval reflects that repeats of one scenario mostly agree.
- pass@k: probability that at least one of k attempts succeeds (unbiased
  estimator from n attempts with c successes).
- pass^k: probability that all k attempts succeed. For an agent that acts on
  production, consistency matters more than a lucky best case.
"""

import random
from collections.abc import Sequence
from math import atan, ceil, comb, cos, pi, sin, sqrt
from statistics import stdev

Z_95 = 1.959964


def wilson(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def cluster_bootstrap(
    clusters: Sequence[Sequence[bool]], draws: int = 10_000, seed: int = 0
) -> tuple[float, float]:
    """95% percentile interval for the pooled pass rate, resampling whole clusters
    (scenarios) with replacement. Seeded, so a report is the same every time it is drawn."""
    sizes = [(sum(c), len(c)) for c in clusters if c]
    # One scenario says nothing about the next one. With only a few, the percentile
    # interval is still too narrow; it is a floor on the uncertainty, not an estimate.
    if len(sizes) < 2:
        return 0.0, 1.0
    passes, runs = sum(p for p, _ in sizes), sum(n for _, n in sizes)
    if passes in (0, runs):  # every resample is 0% or 100%; count scenarios as trials instead
        return wilson(len(sizes) if passes else 0, len(sizes))
    rng = random.Random(seed)
    rates = []
    for _ in range(draws):
        pick = rng.choices(sizes, k=len(sizes))
        rates.append(sum(p for p, _ in pick) / sum(n for _, n in pick))
    rates.sort()
    return rates[int(0.025 * draws)], rates[int(0.975 * draws) - 1]


Runs = dict[str, list[bool]]  # case id to the pass/fail of each of its runs


def rate(runs: Runs, ids: Sequence[str]) -> float:
    """Pooled pass rate over the runs of these cases, repeats included."""
    hits = [x for s in ids for x in runs[s]]
    return sum(hits) / len(hits)


def _same_cases(a: Runs, b: Runs) -> list[str]:
    if sorted(a) != sorted(b):
        raise ValueError("both sides need the same cases")
    return sorted(a)


def paired_gap(a: Runs, b: Runs, draws: int = 10_000, seed: int = 0) -> tuple[float, float, float]:
    """Point gap a - b and its 95% percentile interval, in points. Each draw resamples
    cases with replacement, keeps all of a case's runs, and uses the same draw for both."""
    ids = _same_cases(a, b)
    rng = random.Random(seed)
    gaps = []
    for _ in range(draws):
        pick = rng.choices(ids, k=len(ids))
        gaps.append(rate(a, pick) - rate(b, pick))
    gaps.sort()
    lo, hi = gaps[int(0.025 * draws)], gaps[int(0.975 * draws) - 1]
    return 100 * (rate(a, ids) - rate(b, ids)), 100 * lo, 100 * hi


def diffs(a: Runs, b: Runs) -> list[float]:
    """Per-case pass-rate difference a - b, the paired unit."""
    return [rate(a, [s]) - rate(b, [s]) for s in _same_cases(a, b)]


def _t_cdf(t: float, df: int) -> float:
    """P(T <= t) for t >= 0, from the finite series for integer df
    (Abramowitz and Stegun 26.7.3 and 26.7.4)."""
    theta = atan(t / sqrt(df))
    c2, term, total = cos(theta) ** 2, 1.0, 1.0
    if df % 2 == 0:
        for k in range(1, df // 2):
            term *= c2 * (2 * k - 1) / (2 * k)
            total += term
        return 0.5 + sin(theta) * total / 2
    for k in range(1, (df - 1) // 2):
        term *= c2 * (2 * k) / (2 * k + 1)
        total += term
    series = sin(theta) * cos(theta) * total if df > 1 else 0.0
    return 0.5 + (theta + series) / pi


def t_quantile(q: float, df: int) -> float:
    """Student t quantile for integer df. Not an approximation formula: it bisects the
    exact CDF series, so the only error is the bisection tolerance (below 1e-9)."""
    if not 0 < q < 1 or df < 1:
        raise ValueError(f"need 0 < q < 1 and df >= 1, got q={q} df={df}")
    if q < 0.5:
        return -t_quantile(1 - q, df)
    lo, hi = 0.0, 1.0
    while _t_cdf(hi, df) < q:
        lo, hi = hi, 2 * hi
    while hi - lo > 1e-10:
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _t_cdf(mid, df) < q else (lo, mid)
    return (lo + hi) / 2


def mde80(d: Sequence[float]) -> float:
    """Smallest true gap (points) a paired t-test at 5% two-sided detects 80% of the time,
    for len(d) cases at this sd. Approximate: many d_i are exactly zero, so the d_i are far
    from normal. Zero when no case differs."""
    df = len(d) - 1
    t = t_quantile(0.975, df) + t_quantile(0.80, df)
    return 100 * t * stdev(d) / sqrt(len(d))


def n_for_mde(d: Sequence[float], gap: float) -> int:
    """Cases needed for an MDE80 of `gap` (a fraction) at this sd, normal quantiles."""
    return ceil(((1.960 + 0.842) * stdev(d) / gap) ** 2)


def sign_test(d: Sequence[float]) -> tuple[int, int, float]:
    """Exact two-sided sign test on the cases that differ: (positive, negative, p)."""
    pos, neg = sum(x > 0 for x in d), sum(x < 0 for x in d)
    n = pos + neg
    tail = sum(comb(n, i) for i in range(min(pos, neg) + 1)) / 2**n
    return pos, neg, min(1.0, 2 * tail)


def _check(n: int, c: int, k: int) -> None:
    if not 0 <= c <= n or not 1 <= k <= n:
        raise ValueError(f"need 0 <= c <= n and 1 <= k <= n, got n={n} c={c} k={k}")


def pass_at_k(*, n: int, c: int, k: int) -> float:
    _check(n, c, k)
    return 1.0 - comb(n - c, k) / comb(n, k)


def pass_hat_k(*, n: int, c: int, k: int) -> float:
    _check(n, c, k)
    return comb(c, k) / comb(n, k)


def percentile(values: Sequence[float], q: float) -> float:
    """Linear interpolation between closest ranks, q in [0, 100]."""
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)
