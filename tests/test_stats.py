"""Interval and pass@k arithmetic, checked against hand-computed values."""

import pytest

from rootcause.application.stats import pass_at_k, pass_hat_k, percentile, wilson


def test_wilson_interval_matches_the_closed_form():
    lo, hi = wilson(18, 24)
    assert lo == pytest.approx(0.5509, abs=1e-3)
    assert hi == pytest.approx(0.8800, abs=1e-3)
    assert wilson(0, 24)[0] == 0.0
    assert wilson(24, 24)[1] == pytest.approx(1.0)
    assert wilson(0, 0) == (0.0, 1.0)


def test_pass_at_k_is_any_success_and_pass_hat_k_is_every_success():
    # 3 runs, 2 correct, k=2. Any correct: 1 - C(1,2)/C(3,2) = 1. All correct: C(2,2)/C(3,2).
    assert pass_at_k(n=3, c=2, k=2) == 1.0
    assert pass_hat_k(n=3, c=2, k=2) == pytest.approx(1 / 3)
    assert pass_at_k(n=3, c=0, k=1) == 0.0
    assert pass_hat_k(n=3, c=3, k=3) == 1.0
    with pytest.raises(ValueError):
        pass_hat_k(n=2, c=1, k=3)


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([10], 90) == 10
    assert percentile([], 50) == 0.0
