"""Multi-agent mode end to end, and the comparison table the README's results come from."""

import pytest
from conftest import Scripted, call
from test_orchestrator import DIAGNOSIS, FINDINGS, Router, dispatch
from typer.testing import CliRunner

from rootcause.adapters.sqlite_events import SqliteEventLog
from rootcause.composition import Mode, investigate
from rootcause.interfaces.cli import app


async def test_multi_mode_grades_the_whole_investigation(case):
    provider = Router(
        orchestrator=Scripted(
            [
                {"content": [dispatch("changes")]},
                {"content": [call("submit_diagnosis", DIAGNOSIS, "s")]},
            ]
        ),
        changes=Scripted(
            [
                {"content": [call("diff_config", {"deploy_id": "d-1042"}, "c")]},
                {"content": [call("submit_findings", FINDINGS, "f")]},
            ]
        ),
    )
    run = await investigate(
        case, provider, events=SqliteEventLog(), budget_usd=1.0, mode=Mode.MULTI
    )
    assert run.grade.mode == "multi"
    assert run.grade.passed
    # dispatch_workers in the orchestrator, diff_config in the worker
    assert run.grade.tool_calls == 2


def test_compare_prints_one_row_per_provider_and_mode(scenario_dir, tmp_path):
    one = tmp_path / "one"
    one.mkdir()
    (one / scenario_dir.name).symlink_to(scenario_dir)
    out = CliRunner().invoke(
        app, ["compare", str(one), "--runlog", str(tmp_path / "r.sqlite"), "--mode", "single"]
    )
    assert out.exit_code == 0, out.output
    assert "| heuristic | single | 100% |" in out.output


def test_compare_explains_why_the_heuristic_has_no_multi_row(scenario_dir, tmp_path):
    out = CliRunner().invoke(
        app,
        ["compare", str(scenario_dir.parent), "--runlog", str(tmp_path / "r.sqlite")],
    )
    assert out.exit_code == 0, out.output
    assert "heuristic has no multi-agent mode" in out.output


async def test_unsupported_provider_and_mode_fail_fast(case):
    import pytest

    from rootcause.adapters.heuristic import HeuristicProvider

    with pytest.raises(ValueError, match="no multi-agent mode"):
        await investigate(
            case, HeuristicProvider(), events=SqliteEventLog(), budget_usd=1.0, mode=Mode.MULTI
        )


def test_suite_pass_rate_leaves_out_harness_errors():
    from rootcause.application.evals import Grade, SuiteReport
    from rootcause.domain.answers import Outcome

    def g(sid, outcome, ok):
        return Grade(
            scenario_id=sid, provider="p", mode="single", outcome=outcome, category_ok=ok,
            service_ok=ok, evidence_valid=ok, evidence_hit=ok, invented_evidence=[],
            unapproved_actions=0, steps=1, tool_calls=1, cost_usd=0.0, wall_ms=1.0,
        )  # fmt: skip

    report = SuiteReport(
        provider="p",
        mode="single",
        grades=[g("a", Outcome.ANSWERED, True), g("b", Outcome.ERROR, False)],
    )
    assert report.pass_rate == 1.0
    assert report.errors == 1


async def test_multi_mode_grade_includes_what_the_workers_spent(case):
    provider = Router(
        orchestrator=Scripted(
            [
                {"content": [dispatch("changes")]},
                {"content": [call("submit_diagnosis", DIAGNOSIS, "s")]},
            ]
        ),
        changes=Scripted(
            [
                {"content": [call("diff_config", {"deploy_id": "d-1042"}, "c")]},
                {"content": [call("submit_findings", FINDINGS, "f")]},
            ]
        ),
    )
    run = await investigate(
        case, provider, events=SqliteEventLog(), budget_usd=1.0, mode=Mode.MULTI
    )
    # Scripted turns cost the same each: 2 orchestrator turns + 2 worker turns.
    assert run.grade.cost_usd == pytest.approx(2 * run.result.cost_usd)
    assert run.grade.usage.output_tokens == 2 * run.result.usage.output_tokens
