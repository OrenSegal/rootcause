"""The triage ladder: the rules (and their abstention), the lints, the replay
arithmetic, and `rootcause ladder` on a small hand-made sweep."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rootcause.adapters.fixtures import write_case
from rootcause.adapters.rules import ABSTAINED, RulesEngine
from rootcause.adapters.sqlite_events import SqliteEventLog
from rootcause.application.agent import RunResult
from rootcause.application.diff import RunRecord
from rootcause.application.evals import crash_grade
from rootcause.application.executor import ToolCall
from rootcause.application.ladder import Attempt, climb, replay
from rootcause.application.lints import (
    blamed_commit_unnamed,
    change_without_commit,
    disagrees_with_rule,
    flags,
    lint_rows,
    low_confidence,
    no_failed_job_log_query,
    not_change_but_matrix_wide,
)
from rootcause.application.sweep import Row, SweepConfig, SweepSpec
from rootcause.application.transcript import OUTCOME
from rootcause.application.triage import rule_of, triage
from rootcause.composition import investigate, join_runs, open_events
from rootcause.domain.answers import Diagnosis, Outcome
from rootcause.domain.scenario import Alert, Case, Category, GroundTruth, Scenario
from rootcause.domain.store import TelemetryStore
from rootcause.domain.telemetry import Deploy, LogLine, Span
from rootcause.interfaces.cli import app

T0 = datetime(2026, 11, 1, tzinfo=UTC)
runner = CliRunner(env={"NO_COLOR": "1", "COLUMNS": "200"})


def spans(jobs: dict[str, tuple[str, list[str]]]) -> list[Span]:
    """A run with jobs: name -> (status, failed step names)."""
    out = [Span(span_id="run-1", trace_id="run-1", service="ci", name="CI", start=T0,
                duration_ms=1.0, status="error")]  # fmt: skip
    for i, (name, (status, steps)) in enumerate(jobs.items()):
        job = f"job-{i}"
        service = name.lower().replace(" ", "-").replace("(", "").replace(")", "")
        out.append(Span(span_id=job, trace_id="run-1", parent_id="run-1", service=service,
                        name=name, start=T0, duration_ms=1.0,
                        status="error" if status == "error" else "ok"))  # fmt: skip
        out += [
            Span(span_id=f"{job}-step-{n}", trace_id="run-1", parent_id=job, service=service,
                 name=step, start=T0, duration_ms=1.0, status="error")
            for n, step in enumerate(steps)
        ]  # fmt: skip
    return out


def log(i: int, service: str, message: str) -> LogLine:
    return LogLine(id=f"log-{i:04d}", ts=T0 + timedelta(seconds=i), service=service,
                   level="info", message=message)  # fmt: skip


def commit(sha: str, *files: str) -> Deploy:
    return Deploy(id=sha, service="repo", version=sha, ts=T0, author="a", summary="s",
                  files=list(files))  # fmt: skip


ONE_JOB: dict[str, tuple[str, list[str]]] = {"test": ("error", ["pytest"])}
MATRIX: dict[str, tuple[str, list[str]]] = {
    f"test ({v})": ("error" if v == "3.11" else "ok", []) for v in ("3.11", "3.12", "3.13")
}


# --- rules: each fires on one fixture and not on another


def test_environment_fires_when_every_failed_job_shows_one():
    logs = [log(1, "test", "Set up Python: Could not resolve host: github.com")]
    verdict = triage(spans(ONE_JOB), [commit("c1", "src/a.py")], logs)
    assert verdict is not None and verdict.rule == "environment"
    assert verdict.truth_class == "not_change" and verdict.evidence == ["log-0001"]


def test_environment_holds_back_when_a_commit_edits_a_workflow():
    logs = [log(1, "test", "Set up Python: Could not resolve host: github.com")]
    assert triage(spans(ONE_JOB), [commit("c1", ".github/workflows/ci.yml")], logs) is None


def test_docs_only_fires_when_a_test_job_fails_on_a_docs_change():
    logs = [log(1, "test", "pytest: Process completed with exit code 1.")]
    verdict = triage(spans(ONE_JOB), [commit("c1", "docs/index.rst", "README.md")], logs)
    assert verdict is not None and verdict.rule == "docs_only"


def test_docs_only_holds_back_when_the_docs_job_failed():
    jobs = {"Build docs": ("error", ["sphinx"])}
    logs = [log(1, "build-docs", "sphinx: Process completed with exit code 2.")]
    assert triage(spans(jobs), [commit("c1", "docs/index.rst")], logs) is None


def test_one_commit_named_blames_the_commit_whose_file_fails():
    logs = [log(1, "test", "pytest: FAILED tests/test_a.py::test_x - AssertionError")]
    verdict = triage(spans(ONE_JOB), [commit("c1", "tests/test_a.py"), commit("c2", "b.py")], logs)
    assert verdict is not None and verdict.rule == "one_commit_named"
    assert verdict.truth_class == "change" and verdict.commit == "c1"
    assert verdict.evidence == ["c1", "log-0001"]


def test_one_commit_named_abstains_when_two_commits_are_named_or_the_line_is_a_pass():
    two = [
        log(1, "test", "pytest: FAILED tests/test_a.py::x"),
        log(2, "test", "pytest: FAILED b.py::y"),
    ]
    assert (
        triage(spans(ONE_JOB), [commit("c1", "tests/test_a.py"), commit("c2", "b.py")], two) is None
    )
    passed = [log(1, "test", "pytest: tests/test_a.py::test_x PASSED")]
    assert triage(spans(ONE_JOB), [commit("c1", "tests/test_a.py")], passed) is None


def test_a_file_is_named_whole_not_as_a_suffix():
    logs = [log(1, "test", "pytest: FAILED tests/other_test_a.py::x - AssertionError")]
    assert triage(spans(ONE_JOB), [commit("c1", "test_a.py")], logs) is None


def test_matrix_minority_fires_on_one_failed_job_of_three():
    logs = [log(1, "test-3.11", "pytest: FAILED tests/test_net.py::test_fetch - TimeoutError")]
    verdict = triage(spans(MATRIX), [commit("c1", "src/a.py")], logs)
    assert verdict is not None and verdict.rule == "matrix_minority"


def test_matrix_minority_holds_back_when_half_the_matrix_failed():
    jobs = {"test (3.11)": ("error", []), "test (3.12)": ("ok", [])}
    assert triage(spans(jobs), [commit("c1", "src/a.py")], [log(1, "test-3.11", "x")]) is None


def test_gate_jobs_are_not_counted_as_failures():
    jobs = {**MATRIX, "All tests passed": ("error", ["Set up job"])}
    verdict = triage(spans(jobs), [commit("c1", "src/a.py")], [])
    assert verdict is not None and verdict.rule == "matrix_minority"


# --- the engine: an answer passes the class grade, an abstention is a scored non-pass


def ci_case(sid: str, store: TelemetryStore, truth: GroundTruth) -> Case:
    alert = Alert(severity="ci", service="test", summary="workflow CI failed")
    scenario = Scenario(id=sid, title=sid, alert=alert, truth=truth, provenance="recorded")
    return Case(scenario=scenario, store=store)


def change_case() -> Case:
    store = TelemetryStore(
        logs=[log(1, "test", "pytest: FAILED tests/test_a.py::test_x - AssertionError")],
        metrics=[], spans=spans(ONE_JOB), deploys=[commit("c1", "tests/test_a.py")],
    )  # fmt: skip
    truth = GroundTruth(category=Category.BAD_CODE_DEPLOY, service="repo",
                        required_evidence_any=["c1"], grading="class")  # fmt: skip
    return ci_case("ci-change", store, truth)


def abstain_case() -> Case:
    store = TelemetryStore(
        logs=[log(1, "test", "pytest: Process completed with exit code 1.")],
        metrics=[], spans=spans(ONE_JOB), deploys=[commit("c2", "src/b.py")],
    )  # fmt: skip
    truth = GroundTruth(category=Category.INFRASTRUCTURE, service="repo",
                        required_evidence_any=["log-0001"], grading="class")  # fmt: skip
    return ci_case("ci-abstain", store, truth)


async def test_rules_engine_answers_and_passes_the_class_grade():
    events = open_events()
    run = await investigate(change_case(), RulesEngine(), events=events, budget_usd=0.0)
    assert run.result.outcome is Outcome.ANSWERED and run.grade.passed
    assert (
        run.result.answer is not None and rule_of(run.result.answer.summary) == "one_commit_named"
    )
    assert run.grade.tool_calls == 3 and run.grade.cost_usd == 0.0


async def test_rules_engine_abstains_as_a_scored_non_pass():
    events = open_events()
    run = await investigate(abstain_case(), RulesEngine(), events=events, budget_usd=0.0)
    assert run.result.outcome is Outcome.NO_ANSWER and run.result.answer is None
    assert run.result.note == ABSTAINED
    assert run.grade.status == "scored" and not run.grade.passed
    logged = [e for e in events.events(run.result.run_id) if e.kind == OUTCOME]
    assert logged[0].payload["outcome"] == "no_answer" and logged[0].payload["note"] == ABSTAINED


# --- lints: each flags one answer and passes another


def answer(category: Category, evidence: list[str], confidence: float = 0.9) -> Diagnosis:
    return Diagnosis(category=category, service="repo", summary="s", evidence=evidence,
                     recommended_action="a", confidence=confidence)  # fmt: skip


def query(service: str | None) -> ToolCall:
    args = {} if service is None else {"service": service}
    return ToolCall(step=1, tool_use_id="t", name="query_logs", args=args, gated=False)


CHANGE, INFRA = Category.BAD_CODE_DEPLOY, Category.INFRASTRUCTURE


def test_blamed_commit_unnamed():
    store = change_case().store
    store.deploys.append(commit("c9", "src/other.py"))
    assert blamed_commit_unnamed(store, answer(CHANGE, ["c9"]), [], None)
    assert not blamed_commit_unnamed(store, answer(CHANGE, ["c1"]), [], None)


def test_change_without_commit():
    store = change_case().store
    assert change_without_commit(store, answer(CHANGE, ["log-0001"]), [], None)
    assert not change_without_commit(store, answer(CHANGE, ["c1"]), [], None)
    assert not change_without_commit(store, answer(INFRA, ["log-0001"]), [], None)


def test_no_failed_job_log_query():
    store = change_case().store
    assert no_failed_job_log_query(store, answer(INFRA, ["log-0001"]), [query("lint")], None)
    assert not no_failed_job_log_query(store, answer(INFRA, ["log-0001"]), [query("test")], None)
    assert not no_failed_job_log_query(store, answer(INFRA, ["log-0001"]), [query(None)], None)


def test_low_confidence():
    store = change_case().store
    assert low_confidence(store, answer(CHANGE, ["c1"], confidence=0.5), [], None)
    assert not low_confidence(store, answer(CHANGE, ["c1"], confidence=0.9), [], None)


def test_not_change_but_matrix_wide():
    wide = {f"test ({v})": ("error", ["pytest"]) for v in ("3.11", "3.12")}
    store = TelemetryStore(logs=[], metrics=[], spans=spans(wide), deploys=[])
    assert not_change_but_matrix_wide(store, answer(INFRA, ["job-0"]), [], None)
    narrow = TelemetryStore(logs=[], metrics=[], spans=spans(MATRIX), deploys=[])
    assert not not_change_but_matrix_wide(narrow, answer(INFRA, ["job-0"]), [], None)


def test_disagrees_with_rule():
    store = change_case().store
    rule = answer(CHANGE, ["c1"])
    assert disagrees_with_rule(store, answer(INFRA, ["log-0001"]), [], rule)
    assert not disagrees_with_rule(store, answer(CHANGE, ["c1"]), [], rule)
    assert not disagrees_with_rule(store, answer(INFRA, ["log-0001"]), [], None)


def test_no_answer_has_no_flags_and_lint_rows_count_precision_and_recall():
    assert flags(change_case().store, None, [], None) == []
    answers = [(False, ["low_confidence"]), (True, ["low_confidence"]), (False, [])]
    rows = {r.lint: r for r in lint_rows("m", answers)}
    assert (rows["low_confidence"].precision, rows["low_confidence"].recall) == (0.5, 0.5)


# --- replay arithmetic


def att(passed: bool, cost: float, answered: bool = True, flags: list[str] = []) -> Attempt:  # noqa: B006
    return Attempt(passed=passed, cost=cost, answered=answered, flags=flags)


def test_climb_costs_and_rungs():
    abstain, ok = att(False, 0.0, answered=False), att(True, 0.0)
    assert climb("a", 0, "change", ok, att(False, 1.0), att(True, 5.0)).rung == "rules"
    kept = climb("a", 0, "change", abstain, att(False, 1.0), att(True, 5.0))
    assert (kept.rung, kept.passed, kept.cost) == ("cheap", False, 1.0)
    flagged = att(False, 1.0, flags=["change_without_commit", "low_confidence"])
    up = climb("a", 0, "change", abstain, flagged, att(True, 5.0))
    assert (up.rung, up.passed, up.cost, up.triggers) == (
        "strong",
        True,
        6.0,
        ["change_without_commit"],
    )
    silent = climb("a", 0, "change", abstain, att(False, 1.0, answered=False), att(True, 5.0))
    assert (silent.rung, silent.cost, silent.triggers) == ("strong", 6.0, ["no_answer"])


def test_replay_pairs_rep_i_with_rep_i_and_leaves_out_unpaired_runs():
    rules = {"a": att(False, 0.0, answered=False)}
    flag = ["change_without_commit"]
    cheap = {("a", 0): att(True, 1.0), ("a", 1): att(False, 2.0, flags=flag)}
    strong = {("a", 0): att(False, 10.0), ("a", 1): att(True, 20.0), ("a", 2): att(True, 30.0)}
    r = replay("cheap", "strong", {"a": "change"}, rules, cheap, strong, repeats=3)
    assert [(s.rep, s.rung, s.passed, s.cost) for s in r.steps] == [
        (0, "cheap", True, 1.0), (1, "strong", True, 22.0)
    ]  # fmt: skip
    assert r.skipped == ["a:2 no scored cheap run"]
    ladder = r.arms[-1]
    assert (ladder.name, ladder.runs, ladder.passes, ladder.spent) == ("ladder", 2, 2, 23.0)
    assert ladder.cost_per_solved == 11.5
    by_name = {a.name: a for a in r.arms}
    assert by_name["strong"].spent == 30.0 and by_name["rules"].answered == 0


# --- `rootcause ladder` on a hand-made sweep: join, replay, JSONL


def grade_for(sid: str, provider: str, passed: bool, cost: float, wall: float, cls: str):
    return crash_grade(sid, provider, "single", "").model_copy(update={
        "outcome": Outcome.ANSWERED, "evidence_valid": True, "steps": 2, "tool_calls": 1,
        "cost_usd": cost, "wall_ms": wall, "truth_class": cls, "class_ok": passed,
        "commit_ok": passed, "models": [provider.split(":")[1]], "note": "",
    })  # fmt: skip


def log_run(events: SqliteEventLog, sid: str, provider: str, a: Diagnosis, cost: float,
            wall: float, calls: list[ToolCall]) -> None:  # fmt: skip
    run_id = events.start_run(scenario_id=sid, agent="claude-code", provider=provider)
    result = RunResult[Diagnosis](run_id=run_id, agent="claude-code", outcome=Outcome.ANSWERED,
                                  answer=a, steps=2, tool_calls=calls, cost_usd=cost,
                                  wall_ms=wall)  # fmt: skip
    events.append(run_id, OUTCOME, result.model_dump(mode="json"))


@pytest.fixture
def sweep(tmp_path: Path) -> Path:
    scenarios = tmp_path / "scenarios"
    for case in (change_case(), abstain_case()):
        write_case(case, scenarios / case.scenario.id)
    out = tmp_path / "sweep"
    out.mkdir()
    cheap = SweepConfig(name="cheap", provider="claude-code", model="haiku")
    strong = SweepConfig(name="strong", provider="claude-code", model="opus")
    spec = SweepSpec(name="t", repeats=1, scenarios=str(scenarios), configs=[cheap, strong])
    (out / "spec.json").write_text(spec.model_dump_json())
    events = SqliteEventLog(out / "runs.sqlite")
    hp, op = "claude-code:haiku:high", "claude-code:opus:high"
    looked = [query("test")]
    plan = [  # config, case, provider, answer, passed, cost, wall
        ("cheap", "ci-change", hp, answer(CHANGE, ["c1"]), True, 0.01, 100.0),
        ("strong", "ci-change", op, answer(CHANGE, ["c1"]), True, 0.10, 200.0),
        ("cheap", "ci-abstain", hp, answer(CHANGE, ["log-0001"]), False, 0.02, 300.0),
        ("strong", "ci-abstain", op, answer(INFRA, ["log-0001"]), True, 0.20, 400.0),
    ]
    rows = []
    for config, sid, provider, a, passed, cost, wall in plan:
        log_run(events, sid, provider, a, cost, wall, looked)
        cls = "change" if sid == "ci-change" else "not_change"
        g = grade_for(sid, provider, passed, cost, wall, cls)
        rows.append(Row(config=config, scenario_id=sid, rep=0, grade=g).model_dump_json())
    (out / "rows.jsonl").write_text("\n".join(rows) + "\n")
    return out


def test_join_runs_matches_each_row_to_its_run_and_refuses_ambiguity(sweep: Path):
    from rootcause.adapters.sqlite_events import read_outcomes
    from rootcause.composition import read_sweep

    rows = read_sweep(sweep)[0]
    outcomes = read_outcomes(sweep / "runs.sqlite")
    joined = join_runs(rows, outcomes)
    assert [joined[i]["wall_ms"] for i in range(4)] == [100.0, 200.0, 300.0, 400.0]
    with pytest.raises(ValueError, match="0 logged runs match"):
        join_runs(rows, outcomes[:3])


def test_ladder_command_prints_the_table_and_writes_jsonl(sweep: Path, tmp_path: Path):
    out = tmp_path / "ladder.jsonl"
    before = (sweep / "runs.sqlite").read_bytes()
    result = runner.invoke(
        app, ["ladder", str(sweep), "--cheap", "cheap", "--strong", "strong", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "rules answered 1, cheap kept 0, escalated to strong 1" in result.output
    assert "| ladder | 2 | 2 | 100% | 100% | 100% | 100% | $0.2200 | $0.1100 |" in result.output
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert records == [
        {"case": "ci-abstain", "passed": True, "cost": 0.22, "truth_class": "not_change"},
        {"case": "ci-change", "passed": True, "cost": 0.0, "truth_class": "change"},
    ]
    assert all(RunRecord.model_validate(r) for r in records)
    assert (sweep / "runs.sqlite").read_bytes() == before  # read-only
