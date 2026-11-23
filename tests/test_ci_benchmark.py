"""The frozen CI benchmark (benchmarks/ci): every case loads and grades, the splits share
no repository, labels.csv matches the directories, and nothing the agent can read
reveals the fix commit or the green run. Reads only benchmarks/ci, not scenarios-ci/."""

import csv
import gzip
import importlib.util
import json
from functools import cache
from pathlib import Path

import pytest
from conftest import SCENARIOS

from rootcause.adapters.fixtures import discover, load_case, write_case
from rootcause.application.agent import RunResult
from rootcause.application.evals import grade
from rootcause.domain.answers import Diagnosis, Outcome
from rootcause.domain.scenario import Case, Category

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
    wrong = oracle.model_copy(update={"category": next(c for c in Category if c != truth.category)})
    assert _graded(case, oracle, Outcome.ANSWERED)
    assert not _graded(case, oracle, Outcome.ANSWERED, tool_calls=0)
    assert not _graded(case, None, Outcome.NO_ANSWER)
    assert not _graded(case, wrong, Outcome.ANSWERED)


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


def test_the_split_is_the_seeded_one():
    """The freeze script's split, rerun on labels.csv, gives the committed split."""
    path = ROOT / "scripts" / "freeze_ci_benchmark.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.split(LABELS) == {r["repo"]: r["split"] for r in LABELS}


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
    run = json.loads((PROVENANCE / path.name / "run.json").read_text())
    text = _visible_text(case)
    if fix.get("status") != "rerun":
        for sha in {fix["fixed_sha"], *fix["commits"]}:
            assert sha[:7] not in text, sha
    green_run = fix["fixed_run"].rsplit("/", 1)[-1]
    if green_run != str(run["databaseId"]):
        assert green_run not in text
    root = [span for span in case.store.spans if span.parent_id is None]
    assert len(root) == 1 and root[0].status == "error"


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
