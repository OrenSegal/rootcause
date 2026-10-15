"""Sweeps: repeated runs over several configurations, resumable, summarized with intervals."""

import shutil
from pathlib import Path

import pytest
from conftest import SCENARIOS

from rootcause.adapters.claude_code import ClaudeCodeHarness
from rootcause.adapters.heuristic import HeuristicProvider
from rootcause.application.agent import TokenUsage
from rootcause.application.evals import Grade
from rootcause.application.sweep import (
    Row,
    SweepConfig,
    SweepSpec,
    frontier,
    summarize,
)
from rootcause.composition import read_sweep, run_sweep
from rootcause.domain.answers import Outcome

OPUS = SweepConfig(name="opus-high", provider="claude-code", model="claude-opus-5-5")


def grade(sid: str, passed: bool, cost: float = 0.1, models=("claude-opus-5-5",)) -> Grade:
    return Grade(
        scenario_id=sid, provider="p", mode="single", outcome=Outcome.ANSWERED,
        category_ok=passed, service_ok=True, evidence_valid=True, evidence_hit=True,
        invented_evidence=[], unapproved_actions=0, steps=3, tool_calls=5,
        cost_usd=cost, wall_ms=1000.0 * (2 if passed else 1),
        usage=TokenUsage(output_tokens=100), models=list(models),
    )  # fmt: skip


def rows(config: str, outcomes: dict[str, list[bool]]) -> list[Row]:
    return [
        Row(config=config, scenario_id=sid, rep=rep, grade=grade(sid, ok))
        for sid, oks in outcomes.items()
        for rep, ok in enumerate(oks)
    ]


def test_summary_reports_interval_consistency_and_cost_per_solved_incident():
    s = summarize(OPUS, rows("opus-high", {"a": [True, True, True], "b": [True, False, False]}))
    assert (s.runs, s.scored, s.passes) == (6, 6, 4)
    assert s.pass_rate == pytest.approx(4 / 6)
    assert s.ci_low < s.pass_rate < s.ci_high
    # pass^3: a always passes (1.0), b never passes three times (0.0)
    assert s.pass_hat_k == pytest.approx(0.5)
    assert s.pass_at_k == pytest.approx(1.0)
    assert s.cost_per_pass == pytest.approx(0.6 / 4)
    assert s.p90_wall_s == pytest.approx(2.0)


def test_a_run_served_by_another_model_counts_as_an_error():
    swapped = Row(
        config="opus-high", scenario_id="a", rep=0, grade=grade("a", True, models=["other"])
    )
    s = summarize(OPUS, [swapped, *rows("opus-high", {"b": [True]})])
    assert s.errors == 1
    assert s.scored == 1


def test_frontier_marks_dominated_configs():
    cheap = summarize(OPUS, rows("x", {"a": [True], "b": [False]})).model_copy(
        update={"name": "cheap", "mean_cost": 0.01}
    )
    best = summarize(OPUS, rows("y", {"a": [True], "b": [True]})).model_copy(
        update={"name": "best", "mean_cost": 0.10}
    )
    worse = summarize(OPUS, rows("z", {"a": [True], "b": [False]})).model_copy(
        update={"name": "worse", "mean_cost": 0.20}
    )
    marked = {s.name: s.dominated for s in frontier([cheap, best, worse])}
    assert marked == {"cheap": False, "best": False, "worse": True}


@pytest.fixture
def two_scenarios(tmp_path: Path) -> Path:
    root = tmp_path / "scenarios"
    for name in ["001-checkout-pool-config", "005-inventory-bad-deploy"]:
        shutil.copytree(SCENARIOS / name, root / name)
    return root


async def test_sweep_is_resumable_and_keeps_errors_out_of_the_rows(two_scenarios, tmp_path):
    spec = SweepSpec(
        name="t",
        repeats=2,
        scenarios=str(two_scenarios),
        configs=[
            SweepConfig(name="baseline", provider="heuristic"),
            SweepConfig(name="broken", provider="claude-code", model="claude-opus-5-5"),
        ],
    )

    async def nothing(cmd, cwd):
        return []

    def engine(config: SweepConfig):
        if config.provider == "heuristic":
            return HeuristicProvider()
        return ClaudeCodeHarness(model=config.model, spawn=nothing)

    out = tmp_path / "sweep"
    first = await run_sweep(spec, out, make_engine=engine)
    assert first == {"ran": 8, "skipped": 0}
    done, errors = read_sweep(out)
    assert len(done) == 4 and {r.config for r in done} == {"baseline"}
    assert len(errors) == 4 and {r.config for r in errors} == {"broken"}

    again = await run_sweep(spec, out, make_engine=engine)
    assert again == {"ran": 4, "skipped": 4}  # errors are retried, finished runs are not


def test_cli_plans_runs_a_sweep_and_reports_it(two_scenarios, tmp_path):
    from typer.testing import CliRunner

    from rootcause.interfaces.cli import app

    runner = CliRunner(env={"NO_COLOR": "1", "COLUMNS": "200"})
    spec = tmp_path / "s.yaml"
    spec.write_text(
        f"name: cli\nrepeats: 2\nscenarios: {two_scenarios}\n"
        "configs:\n  - name: baseline\n    provider: heuristic\n"
    )
    out = tmp_path / "out"
    plan = runner.invoke(app, ["sweep", str(spec), "--out", str(out), "--dry-run"])
    assert plan.exit_code == 0, plan.output
    assert "4 runs planned, 0 done, 4 to run" in plan.output
    assert not out.exists()

    ran = runner.invoke(app, ["sweep", str(spec), "--out", str(out)])
    assert ran.exit_code == 0, ran.output
    report = runner.invoke(app, ["report", str(out)])
    assert report.exit_code == 0, report.output
    assert "| baseline | 4 / 0 | 100%" in report.output
    assert (out / "report.md").exists() and (out / "summary.json").exists()
