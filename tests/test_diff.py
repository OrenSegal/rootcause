"""`rootcause diff`: the paired statistics behind it and the verdict it prints."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rootcause.application.diff import RunRecord, compare
from rootcause.application.stats import mde80, paired_gap, sign_test, t_quantile
from rootcause.interfaces.cli import app

FIXTURES = Path(__file__).resolve().parent / "fixtures"
A, B = str(FIXTURES / "diff_a.jsonl"), str(FIXTURES / "diff_b.jsonl")
SWEEP = Path(__file__).resolve().parent.parent / "results/sweeps/models-and-retrieval-2026-10-15"
runner = CliRunner(
    env={"NO_COLOR": "1", "FORCE_COLOR": None, "GITHUB_ACTIONS": None, "COLUMNS": "200"}
)


def side(label: str, outcomes: dict[str, list[bool]]) -> tuple[str, list[RunRecord]]:
    return label, [RunRecord(case=c, passed=p) for c, ps in outcomes.items() for p in ps]


@pytest.mark.parametrize(
    ("q", "df", "table"),
    [(0.975, 1, 12.706), (0.975, 2, 4.303), (0.975, 5, 2.571), (0.975, 10, 2.228),
     (0.975, 24, 2.064), (0.80, 24, 0.857), (0.95, 30, 1.697), (0.975, 120, 1.980)],
)  # fmt: skip
def test_t_quantile_matches_the_table(q, df, table):
    assert t_quantile(q, df) == pytest.approx(table, abs=5e-4)
    assert t_quantile(1 - q, df) == pytest.approx(-table, abs=5e-4)


def test_mde_reproduces_the_25_case_numbers_the_script_printed():
    # 5 of 25 cases differ by a full point: sd = sqrt(0.16 * 25 / 24), MDE 23.85 points
    assert mde80([1.0] * 5 + [0.0] * 20) == pytest.approx(23.85, abs=0.01)
    # With 10 cases the t quantiles are larger: (2.262 + 0.883) * sd / sqrt(10)
    d = [1.0] * 2 + [0.0] * 8
    assert mde80(d) == pytest.approx(100 * 3.1451 * 0.42164 / 10**0.5, abs=0.05)


def test_paired_gap_resamples_cases_with_the_same_draw_for_both_sides():
    # Draws are (x, x) gap 100, (x, y) 50 or (y, y) 0, a quarter of draws at each end.
    a = {"x": [True, True], "y": [True, False]}
    b = {"x": [False, False], "y": [True, False]}
    assert paired_gap(a, b) == (50.0, 0.0, 100.0)
    assert paired_gap(a, a) == (0.0, 0.0, 0.0)


def test_sign_test_is_exact_and_ignores_ties():
    assert sign_test([1.0] * 5 + [0.0] * 20) == (5, 0, 0.0625)
    assert sign_test([1.0] * 6) == (6, 0, 0.03125)
    assert sign_test([1.0, -1.0, 0.0]) == (1, 1, 1.0)


def test_the_three_verdicts():
    base = {f"c{i}": [False] for i in range(8)}
    better = {f"c{i}": [True] for i in range(8)}
    assert compare(side("a", base), side("b", better)).verdict == "b_better"
    assert compare(side("a", better), side("b", base)).verdict == "a_better"
    mixed = {**base, "c0": [True], "c1": [True]}
    r = compare(side("a", base), side("b", mixed))
    assert r.verdict == "no_difference" and r.mde80 is not None and r.cases_for_gap is not None
    assert compare(side("a", base), side("b", base)).mde80 is None


def test_an_interval_that_excludes_zero_is_not_enough_without_the_sign_test():
    # 4 of 25 cases favour B and none favour A: every resample is >= 0, sign p = 0.125.
    a = {f"c{i:02}": [False] for i in range(25)}
    b = {**a, **{f"c{i:02}": [True] for i in range(4)}}
    r = compare(side("a", a), side("b", b))
    assert r.gap_low >= 0 and r.sign_p == 0.125 and r.verdict == "no_difference"


def test_cases_on_one_side_are_an_error_unless_intersect():
    a = {"x": [True], "y": [False], "only-a": [True]}
    b = {"x": [True], "y": [True], "z": [False], "only-b": [True]}
    with pytest.raises(ValueError, match="only-a, only-b, z"):
        compare(side("a", a), side("b", b))
    r = compare(side("a", a), side("b", b), intersect=True)
    assert (r.cases, r.dropped) == (2, 3)


def test_plain_jsonl_and_fail_on_regression_exit_codes():
    out = runner.invoke(app, ["diff", A, B, "--json", "--fail-on-regression"])
    assert out.exit_code == 0, out.output
    r = json.loads(out.output)
    assert r["verdict"] == "b_better" and r["gap"] == pytest.approx(80.0)
    assert (r["a"]["runs"], r["a"]["pass_rate"]) == (10, 0.2)  # the error row is not scored
    assert r["a"]["cost_per_solved"] == pytest.approx(0.11 / 2)  # but its cost is counted
    assert r["b"]["cost_per_solved"] == pytest.approx(0.02)
    assert runner.invoke(app, ["diff", B, A]).exit_code == 0
    worse = runner.invoke(app, ["diff", B, A, "--fail-on-regression"])
    assert worse.exit_code == 1 and "B is a regression" in worse.output


def test_mismatch_from_the_command_line(tmp_path):
    extra = tmp_path / "extra.jsonl"
    extra.write_text(Path(B).read_text() + '{"case": "c11", "passed": true}\n')
    out = runner.invoke(app, ["diff", A, str(extra)])
    assert out.exit_code == 2 and "c11" in out.output
    out = runner.invoke(app, ["diff", A, str(extra), "--intersect"])
    assert out.exit_code == 0 and "Dropped 1 cases" in out.output


def test_sweep_configs_reproduce_the_script_and_name_unknown_ones():
    out = runner.invoke(app, ["diff", f"{SWEEP}:haiku-5.5-high", f"{SWEEP}:opus-5.5-high"])
    assert out.exit_code == 0, out.output
    assert "+1.3 points (-12.0 to +13.3)" in out.output and "19.0 points" in out.output
    assert "no detectable difference" in out.output
    bad = runner.invoke(app, ["diff", f"{SWEEP}:haiku-5.5-high", f"{SWEEP}:nope"])
    assert bad.exit_code == 2 and "opus-5.5-high" in bad.output
