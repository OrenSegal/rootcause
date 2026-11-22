"""A failed GitHub Actions run, mapped onto the same telemetry model as an incident:
job logs become logs, jobs and steps become spans, commits become deploys, and
workflow or config files a commit touched become config changes."""

import json
from pathlib import Path

import pytest
from conftest import SCENARIOS

from rootcause.adapters.fixtures import write_case
from rootcause.adapters.github_actions import (
    Candidate,
    GhCli,
    Label,
    RawRun,
    candidates,
    case_from_raw,
    label,
)
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


def _run(n: int, conclusion: str, sha: str, wf: int = 1, attempt: int = 1) -> dict:
    return {
        "id": n,
        "workflow_id": wf,
        "conclusion": conclusion,
        "head_sha": sha,
        "created_at": f"2026-11-01T00:00:{n:02d}Z",
        "run_attempt": attempt,
    }


def test_only_the_first_red_run_of_a_streak_is_a_case():
    runs = [
        _run(1, "success", "a"),
        _run(2, "failure", "b"),
        _run(3, "failure", "c"),
        _run(4, "cancelled", "d"),
        _run(5, "success", "e"),
        _run(6, "failure", "f"),  # still red at the end of the window: no fix yet
        _run(7, "success", "x", wf=2, attempt=2),
    ]
    found = candidates(runs)
    pair = next(c for c in found if not c.rerun)
    assert pair.green and (pair.green["id"], pair.red["id"], pair.fixed["id"]) == (1, 2, 5)
    rerun = next(c for c in found if c.rerun)
    assert rerun.red["id"] == 7 and rerun.flaky
    assert len(found) == 2


def _commit(sha: str, *files: str) -> dict:
    return {"oid": sha, "files": [{"filename": f, "status": "modified"} for f in files]}


def _raw(*commits: dict, log: str = "", jobs: list | None = None) -> RawRun:
    return RawRun(run={"jobs": jobs or []}, failed_log=log, commits=list(commits))


def _case(red_sha: str = "r", fixed_sha: str = "f") -> Candidate:
    return Candidate(
        green=None, red=_run(1, "failure", red_sha), fixed=_run(2, "success", fixed_sha)
    )


def test_label_rules_first_match_wins():
    commits = [_commit("c1", "src/a.py"), _commit("c2", "src/b.py", ".github/workflows/ci.yml")]
    assert label(_case("same", "same"), _raw(*commits), ["src/a.py"]) == Label(
        "infrastructure", None, "a rerun of the same commit passed"
    )
    dep = label(_case(), _raw(*commits), ["uv.lock", "requirements/dev.txt"])
    assert isinstance(dep, Label) and dep.category == "dependency_failure" and dep.guilty is None
    cfg = label(_case(), _raw(*commits), [".github/workflows/ci.yml"])
    assert isinstance(cfg, Label) and (cfg.category, cfg.guilty) == ("config_change", "c2")
    code = label(_case(), _raw(*commits), ["src/a.py", "tests/test_a.py"])
    assert isinstance(code, Label) and (code.category, code.guilty) == ("bad_code_deploy", "c1")


def test_label_drops_what_the_rules_cannot_decide():
    commits = [_commit("c1", "src/a.py", "CHANGES.md"), _commit("c2", "src/a.py", "uv.lock")]
    assert (
        label(_case(), _raw(*commits), ["src/a.py"])
        == "2 tested commits touched the files the fix changed"
    )
    assert (
        label(_case(), _raw(*commits), ["src/z.py"])
        == "0 tested commits touched the files the fix changed"
    )
    assert (
        label(_case(), _raw(*commits), ["uv.lock"])
        == "a tested commit changed the pins the fix changed"
    )
    assert label(_case(), _raw(*commits), []) == "the fix changed only changelogs or nothing"


def test_sharing_a_changelog_is_not_evidence():
    commits = [_commit("c1", "src/a.py", "CHANGES.md"), _commit("c2", "docs/release-notes.md")]
    assert isinstance(label(_case(), _raw(*commits), ["CHANGES.md", "src/b.py"]), str)
    code = label(_case(), _raw(*commits), ["changelog/1.bugfix.rst", "src/a.py"])
    assert isinstance(code, Label) and code.guilty == "c1"


def test_environment_errors_and_lone_matrix_failures_come_before_the_fix_rule():
    commits = [_commit("c1", "src/a.py")]
    env = label(
        _case(),
        _raw(*commits, log="##[error]The version '3.12.10' with architecture 'x64' was not found"),
        ["src/a.py"],
    )
    assert isinstance(env, Label) and (env.category, env.guilty) == ("infrastructure", None)
    jobs = [
        {"name": "test (3.13, windows-11-arm)", "conclusion": "failure"},
        {"name": "test (3.13, ubuntu-latest)", "conclusion": "success"},
        {"name": "test (3.13, macos-latest)", "conclusion": "success"},
        {"name": "lint", "conclusion": "success"},
    ]
    lone = label(_case(), _raw(*commits, jobs=jobs), ["src/a.py"])
    assert lone == "one matrix job failed while 2 siblings passed (flaky or platform bug)"
    slashed = [
        {"name": "Test windows-latest / pypy3.11", "conclusion": "failure"},
        {"name": "Test macos-latest / 3.13", "conclusion": "success"},
        {"name": "Test ubuntu-latest / 3.14", "conclusion": "success"},
    ]
    assert label(_case(), _raw(*commits, jobs=slashed), ["src/a.py"]) == lone
    gated = [*jobs, {"name": "check", "conclusion": "failure"}]
    assert label(_case(), _raw(*commits, jobs=gated), ["src/a.py"]) == lone


def test_failures_only_in_unreleased_upstream_jobs_are_dropped():
    commits = [_commit("c1", "src/a.py")]
    upstream = [
        {"name": "Docutils HEAD", "conclusion": "failure"},
        {"name": "Aiohttp tests (master)", "conclusion": "failure"},
        {"name": "check", "conclusion": "failure"},
        {"name": "Python 3.13", "conclusion": "success"},
    ]
    assert label(_case(), _raw(*commits, jobs=upstream), ["src/a.py"]) == (
        "only jobs testing an unreleased upstream failed"
    )
    mixed = [*upstream, {"name": "Python 3.14", "conclusion": "failure"}]
    code = label(_case(), _raw(*commits, jobs=mixed), ["src/a.py"])
    assert isinstance(code, Label) and code.guilty == "c1"
    # "Headers" and "development docs" must not read as HEAD or dev.
    named = [{"name": "Headers lint", "conclusion": "failure"}]
    assert isinstance(label(_case(), _raw(*commits, jobs=named), ["src/a.py"]), Label)


def test_rules_added_after_the_third_validation():
    commits = [_commit("c1", ".github/workflows/test.yml"), _commit("c2", "src/a.py")]
    missing = "##[error]The version '3.15' with architecture 'x64' was not found"
    cfg = label(_case(), _raw(*commits, log=missing), [".github/workflows/test.yml"])
    assert isinstance(cfg, Label) and (cfg.category, cfg.guilty) == ("config_change", "c1")
    env = label(_case(), _raw(*commits, log=missing), ["src/a.py"])
    assert isinstance(env, Label) and (env.category, env.guilty) == ("infrastructure", None)
    apt = "E: Failed to fetch http://archive.ubuntu.com/x.deb  Connection failed [IP: 1.2.3.4 80]"
    assert label(_case(), _raw(*commits, log=apt), ["src/a.py"]).category == "infrastructure"
    crash = "FAILED t.py::test_x - worker 'gw3' crashed while running 't.py::test_x'"
    assert label(_case(), _raw(*commits, log=crash), ["src/a.py"]).startswith("test workers")
    merged = label(_case(), _raw(*commits), ["src/a.py"], fix_merged=True)
    assert merged == "the fix merged another branch, so its diff is not only the fix"


def test_rules_added_after_the_fourth_validation():
    commits = [_commit("c1", "src/a.py", "docs/a.md")]
    colon = [
        {"name": "ui:test-ui:windows-latest", "conclusion": "failure"},
        {"name": "ui:test-ui:macos-latest", "conclusion": "success"},
        {"name": "ui:test-ui:ubuntu-latest", "conclusion": "success"},
        {"name": "result:test", "conclusion": "failure"},
    ]
    assert label(_case(), _raw(*commits, jobs=colon), ["src/a.py"]) == (
        "one matrix job failed while 2 siblings passed (flaky or platform bug)"
    )
    mixed = [
        {"name": "test (py39)", "conclusion": "failure"},
        {"name": "test (py310, windows)", "conclusion": "failure"},
    ]
    log = (
        "test (py39)\tsetup\tZ The version '3.9' with architecture 'x64' was not found\n"
        "test (py310, windows)\tpytest\t2026Z E   AssertionError: assert 0 == 1\n"
    )
    code = label(_case(), _raw(*commits, log=log, jobs=mixed), ["src/a.py"])
    assert isinstance(code, Label) and code.guilty == "c1"
    tests_only = [{"name": "test (3.13)", "conclusion": "failure"}]
    docs = label(_case(), _raw(*commits, jobs=tests_only), ["docs/a.md"])
    assert (
        docs == "the fix shares only documentation with the tested commit, and no docs job failed"
    )
    docs_job = [{"name": "Build docs", "conclusion": "failure"}]
    assert isinstance(label(_case(), _raw(*commits, jobs=docs_job), ["docs/a.md"]), Label)


def test_each_pull_request_branch_is_its_own_timeline():
    runs = [
        _run(1, "success", "a") | {"head_branch": "pr-1"},
        _run(2, "failure", "b") | {"head_branch": "pr-2"},
        _run(3, "failure", "c") | {"head_branch": "pr-1"},
        _run(4, "success", "d") | {"head_branch": "pr-2"},
        _run(5, "success", "e") | {"head_branch": "pr-1"},
    ]
    found = {c.red["id"]: (c.green and c.green["id"], c.fixed["id"]) for c in candidates(runs)}
    assert found == {2: (None, 4), 3: (1, 5)}


def test_unknown_step_names_are_recovered_from_step_timings():
    run = {
        "jobs": [
            {
                "name": "test",
                "steps": [
                    {"name": "setup", "startedAt": "2026-11-01T00:00:00Z",
                     "completedAt": "2026-11-01T00:00:04Z"},
                    {"name": "pytest", "startedAt": "2026-11-01T00:00:05Z",
                     "completedAt": "2026-11-01T00:00:09Z"},
                ],
            }
        ]
    }  # fmt: skip
    log = "test\tUNKNOWN STEP\t2026-11-01T00:00:06.5Z ##[error]boom\n"
    logs = case_from_raw(RawRun(run=_full_run(run), failed_log=log, commits=[])).store.logs
    assert [entry.message for entry in logs] == ["pytest: boom"]


def _full_run(run: dict) -> dict:
    job = run["jobs"][0] | {
        "databaseId": 1, "conclusion": "failure",
        "startedAt": "2026-11-01T00:00:00Z", "completedAt": "2026-11-01T00:00:09Z",
    }  # fmt: skip
    job["steps"] = [
        st | {"number": n, "conclusion": "success"} for n, st in enumerate(job["steps"])
    ]
    return {
        "databaseId": 9, "name": "ci", "displayTitle": "t", "headBranch": "b",
        "headSha": "0" * 40, "conclusion": "failure", "createdAt": "2026-11-01T00:00:00Z",
        "updatedAt": "2026-11-01T00:00:09Z", "jobs": [job],
    }  # fmt: skip


def test_not_found_errors_in_test_output_are_not_network_errors():
    commits = [_commit("c1", "src/a.py")]
    for text in ("E   ModuleNotFoundError: No module named 'resource'", "FileNotFoundError: x"):
        found = label(_case(), _raw(*commits, log=text), ["src/a.py"])
        assert isinstance(found, Label) and found.category == "bad_code_deploy", text
    net = label(_case(), _raw(*commits, log="getaddrinfo ENOTFOUND api.github.com"), ["src/a.py"])
    assert isinstance(net, Label) and net.category == "infrastructure"


def test_a_pull_request_rerun_is_not_evidence_of_a_flake():
    red = _run(1, "failure", "s") | {"event": "pull_request"}
    pr = Candidate(green=None, red=red, fixed=_run(2, "success", "s"))
    assert label(pr, _raw(), []) == (
        "a pull-request rerun passed, but it tested a new merge with the base"
    )
    push = Candidate(green=None, red=red | {"event": "push"}, fixed=_run(2, "success", "s"))
    assert isinstance(label(push, _raw(), []), Label)
