"""Small-sample statistics for eval results.

With a few dozen scenarios, a pass rate on its own hides how wide the
uncertainty is. These are the estimators the reports use:

- Wilson score interval: a binomial confidence interval that stays sensible
  near 0% and 100%, where the normal approximation does not.
- pass@k: probability that at least one of k attempts succeeds (unbiased
  estimator from n attempts with c successes).
- pass^k: probability that all k attempts succeed. For an agent that acts on
  production, consistency matters more than a lucky best case.
"""

from collections.abc import Sequence
from math import comb, sqrt

Z_95 = 1.959964


def wilson(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


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
