"""A failed GitHub Actions run, mapped onto the same telemetry model as an incident:
job logs become logs, jobs and steps become spans, commits become deploys, and
workflow or config files a commit touched become config changes."""

import json
from pathlib import Path

import pytest
from conftest import SCENARIOS

from rootcause.adapters.fixtures import write_case
from rootcause.adapters.github_actions import GhCli, RawRun, case_from_raw
from rootcause.adapters.heuristic import HeuristicProvider
from rootcause.adapters.sqlite_events import SqliteEventLog
from rootcause.composition import triage_ci

RECORDED = SCENARIOS / "008-sous-shellcheck"


@pytest.fixture
def raw() -> RawRun:
    return RawRun.load(RECORDED / "raw")


def test_log_lines_get_levels_ids_and_clean_text(raw):
    logs = case_from_raw(raw).store.logs
    by_text = {log.message: log for log in logs}
    sc = next(log for log in logs if "SC2120" in log.message)
    assert sc.level == "warn" and sc.service == "test-ubuntu-latest"
    assert "\x1b" not in "".join(by_text) and "﻿" not in "".join(by_text)
    assert any(log.level == "error" and "exit code 1" in log.message for log in logs)
    assert len({log.id for log in logs}) == len(logs)


def test_commits_after_the_tested_head_are_not_suspects(raw):
    shas = [d.id for d in case_from_raw(raw).store.deploys]
    assert shas == ["afdd3f008b", "4870121d6d"]  # the later fix commit is excluded


def test_workflow_changes_are_config_changes(raw):
    store = case_from_raw(raw).store
    keys = [c.key for c in store.diff_config("4870121d6d")]
    assert keys == [".github/workflows/ci.yml"]
    deploy = store.get_deploy("4870121d6d")
    assert deploy is not None and "tests/gate.test.sh" in deploy.files


def test_jobs_and_steps_become_a_trace(raw):
    store = case_from_raw(raw).store
    spans = store.get_trace("run-36960431348")
    failed = [s for s in spans if s.status == "error"]
    assert [s.name for s in failed] == ["ci", "test (ubuntu-latest)", "shellcheck"]


def test_alert_names_the_failed_job(raw):
    alert = case_from_raw(raw).scenario.alert
    assert alert.service == "test-ubuntu-latest"
    assert "shellcheck" in alert.summary


def test_recorded_fixtures_match_a_fresh_derivation(raw, tmp_path: Path):
    truth = case_from_raw(raw).scenario.truth  # None: truth is added by hand
    assert truth is None
    fresh = write_case(case_from_raw(raw), tmp_path / "008")
    for name in ["logs.jsonl", "metrics.json", "traces.json", "deploys.json"]:
        assert (fresh / name).read_text() == (RECORDED / name).read_text(), name


async def test_recorder_uses_only_read_only_gh_calls(tmp_path: Path):
    issued: list[list[str]] = []
    recorded = RECORDED / "raw"

    def fake_gh(args: list[str]) -> str:
        issued.append(args)
        if args[:2] == ["run", "view"] and "--log-failed" in args:
            return (recorded / "failed.log").read_text()
        if args[:2] == ["run", "view"]:
            return (recorded / "run.json").read_text()
        if args[:2] == ["pr", "list"]:
            return json.dumps([{"number": 5}])
        if args[:2] == ["pr", "view"]:
            commits = json.loads((recorded / "commits.json").read_text())
            return json.dumps(
                {
                    "commits": [
                        {k: c[k] for k in c if k != "files"} | {"authors": [{"login": c["author"]}]}
                        for c in commits
                    ]
                }
            )
        if args[0] == "api":
            sha = args[1].rsplit("/", 1)[-1]
            commit = next(
                c for c in json.loads((recorded / "commits.json").read_text()) if c["oid"] == sha
            )
            return json.dumps(commit["files"])
        raise AssertionError(args)

    raw = GhCli(runner=fake_gh).fetch("OrenSegal/sous", 36960431348)
    assert raw == RawRun.load(recorded)
    verbs = {tuple(a[:2]) for a in issued if a[0] != "api"} | {
        ("api", "GET") for a in issued if a[0] == "api"
    }
    assert verbs <= {("run", "view"), ("pr", "list"), ("pr", "view"), ("api", "GET")}
    assert all("--method" not in a and "-X" not in a for a in issued)


async def test_triage_runs_an_investigation_without_ground_truth(raw):
    run = await triage_ci(
        case_from_raw(raw), HeuristicProvider(), events=SqliteEventLog(), budget_usd=1.0
    )
    assert run.answer is not None
    # The baseline blames the workflow edit; the real cause is the new test script.
    assert run.answer.category == "config_change"
