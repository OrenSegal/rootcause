"""The frozen CI benchmark (benchmarks/ci): every case loads and grades, the splits share
no repository, labels.csv matches the directories, and nothing the agent can read
reveals the fix commit or the green run. Reads only benchmarks/ci, not scenarios-ci/."""

import csv
import gzip
import importlib.util
import json
from collections import Counter
from functools import cache
from pathlib import Path

import pytest
from conftest import SCENARIOS

from rootcause.adapters.fixtures import discover, load_case, write_case
from rootcause.application.agent import RunResult
from rootcause.application.evals import grade
from rootcause.domain.answers import Diagnosis, Outcome
from rootcause.domain.scenario import Case, Category, category_class

ROOT = SCENARIOS.parent
BENCH = ROOT / "benchmarks" / "ci"
PROVENANCE = BENCH / "provenance"
SPLITS = ("dev", "test")
CASES = [p for s in SPLITS for p in discover(BENCH / s)]
LABELS = list(csv.DictReader((BENCH / "labels.csv").open()))


@cache
def _case(path: Path) -> Case:
    return load_case(path)


def _graded(case: Case, answer: Diagnosis | None, outcome: Outcome, tool_calls: int = 1) -> bool:
    result = RunResult[Diagnosis](
        run_id="r", agent="a", outcome=outcome, answer=answer, steps=1, tool_calls=[],
        cost_usd=0.0, wall_ms=0.0,
    )  # fmt: skip
    return grade(case, result, provider="p", mode="single", tool_calls=tool_calls).passed


def test_both_splits_have_cases():
    assert discover(BENCH / "dev") and discover(BENCH / "test")


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.name)
def test_case_loads_and_its_truth_cites_evidence_that_exists(path: Path):
    case = _case(path)
    assert case.scenario.truth is not None
    assert case.store.logs
    assert case.unknown_truth_evidence() == []


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.name)
def test_the_grader_passes_the_oracle_and_fails_no_answer_and_a_wrong_category(path: Path):
    case = _case(path)
    truth = case.scenario.truth
    assert truth is not None
    oracle = Diagnosis(
        category=truth.category, service=truth.service, summary="Oracle answer.",
        evidence=truth.required_evidence_any[:1], recommended_action="n/a", confidence=1.0,
    )  # fmt: skip
    other_class = next(c for c in Category if category_class(c) not in (_class(case), None))
    wrong = oracle.model_copy(update={"category": other_class})
    assert _graded(case, oracle, Outcome.ANSWERED)
    assert not _graded(case, oracle, Outcome.ANSWERED, tool_calls=0)
    assert not _graded(case, None, Outcome.NO_ANSWER)
    assert not _graded(case, wrong, Outcome.ANSWERED)


def _class(case: Case) -> str | None:
    truth = case.scenario.truth
    return category_class(truth.category) if truth else None


def _first(truth_class: str) -> Case:
    """The first case of a class, chosen at run time: the directories get regenerated."""
    return next(c for c in map(_case, CASES) if _class(c) == truth_class)


def _answer(category: Category, evidence: list[str]) -> Diagnosis:
    return Diagnosis(
        category=category, service="wrong-service", summary="s", evidence=evidence,
        recommended_action="n/a", confidence=0.5,
    )  # fmt: skip


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.name)
def test_every_case_is_class_graded(path: Path):
    truth = _case(path).scenario.truth
    assert truth is not None and truth.grading == "class"


def test_a_not_change_case_passes_on_any_real_evidence_with_a_not_change_category():
    case = _first("not_change")
    truth = case.scenario.truth
    assert truth is not None
    other_log = next(x.id for x in case.store.logs if x.id not in truth.required_evidence_any)
    commit = case.store.deploys[0].id
    flake = _answer(Category.INFRASTRUCTURE, [other_log])
    assert _graded(case, flake, Outcome.ANSWERED)
    assert _graded(case, flake.model_copy(update={"category": Category.DEPENDENCY_FAILURE}),
                   Outcome.ANSWERED)  # fmt: skip
    # Citing a commit to say what it did not touch is still a correct answer.
    assert _graded(case, _answer(Category.INFRASTRUCTURE, [other_log, commit]), Outcome.ANSWERED)
    assert not _graded(case, _answer(Category.BAD_CODE_DEPLOY, [other_log]), Outcome.ANSWERED)
    assert _graded(case, _answer(Category.NO_INCIDENT, [other_log]), Outcome.ANSWERED)
    assert not _graded(case, _answer(Category.INFRASTRUCTURE, ["log-none"]), Outcome.ANSWERED)


def test_a_change_case_passes_with_either_change_category_and_fails_on_the_wrong_commit():
    case = _first("change")
    truth = case.scenario.truth
    assert truth is not None
    guilty = truth.required_evidence_any[0]
    for category in (Category.CONFIG_CHANGE, Category.BAD_CODE_DEPLOY):
        assert _graded(case, _answer(category, [guilty]), Outcome.ANSWERED)
    assert not _graded(case, _answer(Category.INFRASTRUCTURE, [guilty]), Outcome.ANSWERED)
    log = case.store.logs[0].id
    assert not _graded(case, _answer(Category.BAD_CODE_DEPLOY, [log]), Outcome.ANSWERED)
    multi = next(c for c in map(_case, CASES) if _class(c) == "change" and len(c.store.deploys) > 1)
    multi_truth = multi.scenario.truth
    assert multi_truth is not None
    innocent = next(
        d.id for d in multi.store.deploys if d.id not in multi_truth.required_evidence_any
    )
    assert not _graded(multi, _answer(Category.BAD_CODE_DEPLOY, [innocent]), Outcome.ANSWERED)


def test_the_class_grade_records_what_a_report_needs():
    case = _first("not_change")
    log = case.store.logs[0].id
    result = RunResult[Diagnosis](
        run_id="r", agent="a", outcome=Outcome.ANSWERED,
        answer=_answer(Category.RESOURCE_EXHAUSTION, [log]), steps=1, tool_calls=[],
        cost_usd=0.0, wall_ms=0.0,
    )  # fmt: skip
    g = grade(case, result, provider="p", mode="single", tool_calls=1, strict_service=True)
    assert (g.truth_class, g.class_ok, g.commit_ok) == ("not_change", True, True)
    assert not g.category_ok and not g.service_ok
    assert g.passed and g.passes(strict_service=True)


def test_dev_and_test_share_no_repository():
    repos = {s: {r["repo"] for r in LABELS if r["split"] == s} for s in SPLITS}
    assert repos["dev"] and repos["test"]
    assert not repos["dev"] & repos["test"]


def test_both_splits_hold_both_classes():
    for s in SPLITS:
        assert {r["class"] for r in LABELS if r["split"] == s} == {"change", "not_change"}


def test_labels_csv_matches_the_directories():
    for s in SPLITS:
        assert {r["id"] for r in LABELS if r["split"] == s} == {p.name for p in discover(BENCH / s)}
    assert len({r["id"] for r in LABELS}) == len(LABELS)
    for row in LABELS:
        case = _case(BENCH / row["split"] / row["id"])
        truth = case.scenario.truth
        assert truth is not None
        assert case.scenario.id == row["id"]
        assert truth.category == row["category"]
        assert truth.service == row["repo"].rsplit("/", 1)[-1]
        deploy_ids = [d.id for d in case.store.list_deploys()]
        assert str(len(deploy_ids)) == row["commits_under_test"]
        if row["class"] == "change":
            assert truth.required_evidence_any == [row["guilty_commit"]]
            assert row["guilty_commit"] in deploy_ids
        else:
            assert row["guilty_commit"] == ""
            assert not set(truth.required_evidence_any) & set(deploy_ids)


@cache
def _freeze():
    path = ROOT / "scripts" / "freeze_ci_benchmark.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_split_is_the_seeded_one():
    """The freeze script's split, rerun on labels.csv, gives the committed split."""
    assert _freeze().split(LABELS) == {r["repo"]: r["split"] for r in LABELS}


def test_dev_holds_about_a_third_of_not_change_and_a_quarter_of_change():
    freeze = _freeze()
    for c, share in freeze.DEV_SHARE.items():
        total = sum(r["class"] == c for r in LABELS)
        dev = sum(r["class"] == c and r["split"] == "dev" for r in LABELS)
        assert abs(dev - total * share) <= freeze.DEV_SLACK, c


def test_every_label_has_a_source_that_verdicts_csv_backs():
    """rule: the review recorded the rule's label as right. review: it recorded the rule's
    label as wrong and verdicts.csv holds the label the scope document states. rerun: a
    re-run of the same pull-request run passed, and the review confirmed the log."""
    verdicts = {r["case_id"]: r for r in csv.DictReader((BENCH / "verdicts.csv").open())}
    for row in LABELS:
        v = verdicts[row["id"]]
        assert row["label_source"] == v["label_source"]
        assert row["class"] == v["table_class"] or v["table_class"] == ""
        if row["label_source"] == "review":
            assert v["verdict"] == "wrong" and v["note"]
            assert row["category"] == v["table_category"]
            assert row["guilty_commit"] == v["table_commit"]
        else:
            assert v["verdict"] == "right"
        if row["label_source"] == "rerun":
            assert row["round"] == "rerun" and row["class"] == "not_change" and v["note"]


def test_rerun_cases_come_from_repositories_no_other_case_does_and_at_most_four_each():
    rerun = Counter(r["repo"] for r in LABELS if r["label_source"] == "rerun")
    others = {r["repo"] for r in LABELS if r["label_source"] != "rerun"}
    assert rerun and not set(rerun) & others
    assert max(rerun.values()) <= _freeze().MAX_RERUN_PER_REPO


def test_a_rerun_trace_gets_the_failed_attempt_root_status_back():
    """GitHub can rewrite a failed attempt's conclusion to success once a re-run passes;
    its jobs keep theirs. The freeze sets the root span back to error for re-run cases."""
    repaired = _freeze().repaired_traces
    root = {"span_id": "run-1", "parent_id": None, "status": "ok"}
    job = {"span_id": "job-2", "parent_id": "run-1", "status": "error"}
    step = {"span_id": "job-2-step-1", "parent_id": "job-2", "status": "error"}
    rerun, red_to_green = {"status": "rerun"}, {"status": "ahead"}
    assert repaired([root, job, step], rerun)[0]["status"] == "error"
    assert repaired([root, job, step], red_to_green)[0]["status"] == "ok"
    passed = job | {"status": "ok"}
    assert repaired([root, passed, step], rerun)[0]["status"] == "ok"


def _visible_text(case: Case) -> str:
    """What the agent can read: the alert, the title and everything the store serves."""
    s = case.store
    parts = [case.scenario.title, case.scenario.alert.render()]
    parts += [log.model_dump_json() for log in s.logs]
    parts += [span.model_dump_json() for span in s.spans]
    parts += [d.model_dump_json() for d in s.list_deploys()]
    parts += [m.model_dump_json() for m in s.metrics]
    return "\n".join(parts)


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.name)
def test_nothing_the_agent_reads_reveals_the_fix_or_the_green_run(path: Path):
    """A rerun's green commit is the failed commit and its green run has the same id,
    so only non-rerun cases are checked for those. The trace's root span must say the
    run failed: GitHub rewrites it to success when a rerun passes."""
    case = _case(path)
    fix = json.loads((PROVENANCE / path.name / "fix.json").read_text())
    run = json.loads(gzip.decompress((PROVENANCE / path.name / "run.json.gz").read_bytes()))
    text = _visible_text(case)
    if fix.get("status") != "rerun":
        for sha in {fix["fixed_sha"], *fix["commits"]}:
            assert sha[:7] not in text, sha
    green_run = fix["fixed_run"].rsplit("/", 1)[-1]
    if green_run != str(run["databaseId"]):
        assert green_run not in text
    root = [span for span in case.store.spans if span.parent_id is None]
    assert len(root) == 1 and root[0].status == "error"
    if fix.get("status") == "rerun":
        # The run's final (passing) attempt must not show: the trace is attempt 1's, so
        # it ends when attempt 1's last job did, and no span says the run passed.
        jobs = [span for span in case.store.spans if span.parent_id == root[0].span_id]
        assert any(span.status == "error" for span in jobs)
        end = max(span.start.timestamp() * 1000 + span.duration_ms for span in jobs)
        root_end = root[0].start.timestamp() * 1000 + root[0].duration_ms
        assert root_end <= end + 60_000


def test_provenance_is_out_of_reach_of_the_loader():
    """The mined header (fix run, label rule) lives only in provenance/, which no
    scenario discovery picks up, and the committed scenario files carry no comments."""
    assert discover(BENCH) == [] and discover(PROVENANCE) == []
    assert {p.name for p in PROVENANCE.iterdir()} == {r["id"] for r in LABELS}
    for path in CASES:
        assert not (path / "scenario.yaml").read_text().startswith("#")
        assert not (path / "raw").exists()


def test_logs_load_from_gzip_and_the_plain_file_wins(tmp_path: Path):
    case = load_case(SCENARIOS / "008-sous-shellcheck")
    out = write_case(case, tmp_path / "c")
    plain = (out / "logs.jsonl").read_bytes()
    (out / "logs.jsonl.gz").write_bytes(gzip.compress(plain, mtime=0))
    (out / "logs.jsonl").unlink()
    assert load_case(out).store.logs == case.store.logs
    (out / "logs.jsonl").write_text(case.store.logs[0].model_dump_json() + "\n")
    assert load_case(out).store.logs == case.store.logs[:1]
