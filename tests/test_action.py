"""The GitHub Action: inputs must reach the CLI as data, and its defaults must pass."""

import os
import re
import stat
import subprocess
from pathlib import Path

import yaml
from conftest import SCENARIOS

from rootcause.adapters.heuristic import HeuristicProvider
from rootcause.adapters.sqlite_events import SqliteEventLog
from rootcause.composition import run_suite

ACTION = yaml.safe_load((SCENARIOS.parent / "action.yml").read_text())
STEP = next(s for s in ACTION["runs"]["steps"] if s.get("id") == "eval")


def test_no_expression_is_expanded_inside_the_shell_script():
    assert "${{" not in STEP["run"]


def test_hostile_inputs_reach_the_cli_as_plain_arguments(tmp_path: Path):
    canary = tmp_path / "pwned"
    hostile = f'x"; touch {canary}; echo "'
    fake_uv = tmp_path / "uv"
    fake_uv.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$RUNNER_TEMP/argv"\n')
    fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IEXEC)

    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "GITHUB_WORKSPACE": str(tmp_path),
    }
    for name, value in STEP["env"].items():
        env[name] = hostile if re.search(r"inputs\.", str(value)) else str(value)
    subprocess.run(["bash", "-e", "-c", STEP["run"]], env=env, check=True)

    argv = (tmp_path / "argv").read_text().splitlines()
    assert not canary.exists()
    for flag in ("--provider", "--model", "--effort", "--mode", "--budget-usd", "--min-pass-rate"):
        assert argv[argv.index(flag) + 1] == hostile
    # A scenarios path can't turn into an option of the CLI.
    assert argv[-2:] == ["--", hostile]


async def test_the_default_bar_passes_for_the_default_provider():
    assert ACTION["inputs"]["provider"]["default"] == "heuristic"
    report = await run_suite(SCENARIOS, HeuristicProvider, events=SqliteEventLog(), budget_usd=1.0)
    assert report.pass_rate >= float(ACTION["inputs"]["min-pass-rate"]["default"])


# --- The triage and diff actions, and the workflows that ship or use them.

ROOT = SCENARIOS.parent
TRIAGE = yaml.safe_load((ROOT / "triage" / "action.yml").read_text())
DIFF = yaml.safe_load((ROOT / "diff" / "action.yml").read_text())
WORKFLOW_FILES = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
WORKFLOWS = {p.stem: yaml.safe_load(p.read_text()) for p in WORKFLOW_FILES}
TRIAGE_STEP = next(s for s in TRIAGE["runs"]["steps"] if s.get("id") == "triage")
COMMENT_STEP = next(s for s in TRIAGE["runs"]["steps"] if "gh pr comment" in s.get("run", ""))
DIFF_STEP = next(s for s in DIFF["runs"]["steps"] if s.get("id") == "diff")
RECORD_ARGV = 'printf \'%s\\n\' "$@" > "$RUNNER_TEMP/argv"'


def _fake(tmp_path: Path, name: str, body: str) -> None:
    tool = tmp_path / name
    tool.write_text(f"#!/bin/sh\n{body}\n")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)


def _env(tmp_path: Path, step: dict, value) -> dict[str, str]:
    """The step's env, with each input replaced by value(input name)."""
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "GITHUB_ACTION_PATH": str(tmp_path / "action"),
    }
    for name, raw in step["env"].items():
        match = re.search(r"inputs\.([\w-]+)", str(raw))
        env[name] = value(match[1]) if match else str(raw)
    return env


def _run(step: dict, env: dict[str, str]) -> int:
    return subprocess.run(["bash", "-e", "-c", step["run"]], env=env).returncode


def _argv(tmp_path: Path) -> list[str]:
    return (tmp_path / "argv").read_text().splitlines()


def test_no_expression_is_expanded_inside_any_action_script():
    for action in (TRIAGE, DIFF):
        for step in action["runs"]["steps"]:
            assert "${{" not in step.get("run", "")


def test_every_third_party_action_is_pinned_to_a_commit():
    files = [ROOT / "action.yml", ROOT / "triage/action.yml", ROOT / "diff/action.yml"]
    lines = [line.strip() for p in [*files, *WORKFLOW_FILES] for line in p.read_text().splitlines()]
    uses = [x.split("uses: ", 1)[1] for x in lines if "uses: " in x and "uses: ./" not in x]
    assert len(uses) >= 8
    for use in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d[\w.]*", use), use


def test_triage_hostile_inputs_reach_the_cli_as_plain_arguments(tmp_path: Path):
    canary = tmp_path / "pwned"
    hostile = f'x"; touch {canary}; echo "'
    _fake(tmp_path, "uv", f'{RECORD_ARGV}\ntouch "$RUNNER_TEMP/rootcause-triage.md"')
    assert _run(TRIAGE_STEP, _env(tmp_path, TRIAGE_STEP, lambda _: hostile)) == 0
    argv = _argv(tmp_path)
    assert not canary.exists()
    for flag in ("--repo", "--run", "--provider", "--model", "--budget-usd"):
        assert argv[argv.index(flag) + 1] == hostile
    # The tool comes from the action's own ref, not from the caller's checkout.
    assert argv[:4] == ["run", "--locked", "--project", f"{tmp_path}/action/.."]


def test_triage_defaults_to_the_free_provider_and_no_comment():
    inputs = TRIAGE["inputs"]
    assert inputs["provider"]["default"] == "rules"
    assert inputs["comment"]["default"] == "false"
    assert COMMENT_STEP["if"] == "inputs.comment == 'true'"
    # It never checks out, so it never runs the pull request's code.
    assert not any("checkout" in s.get("uses", "") for s in TRIAGE["runs"]["steps"])


def test_triage_passes_on_an_abstention_and_fails_on_an_error(tmp_path: Path):
    def run(status: int) -> int:
        _fake(tmp_path, "uv", f'echo verdict > "$RUNNER_TEMP/rootcause-triage.md"\nexit {status}')
        return _run(TRIAGE_STEP, _env(tmp_path, TRIAGE_STEP, lambda _: "1"))

    assert run(0) == 0
    assert run(2) == 0  # no rule fired: the verdict says so, and the job is not failed twice
    assert run(1) == 1
    assert (tmp_path / "summary").read_text().count("verdict") == 3


def test_the_comment_goes_only_to_a_numbered_pull_request(tmp_path: Path):
    _fake(tmp_path, "gh", RECORD_ARGV)
    for pr in ("", "1; touch pwned", "$(id)"):
        env = _env(tmp_path, COMMENT_STEP, lambda name, pr=pr: pr if name == "pr-number" else "x")
        assert _run(COMMENT_STEP, env) == 0
        assert not (tmp_path / "argv").exists()
    assert "no comment was posted" in (tmp_path / "summary").read_text()
    env = _env(tmp_path, COMMENT_STEP, lambda name: "12" if name == "pr-number" else "o/r")
    assert _run(COMMENT_STEP, env) == 0
    assert _argv(tmp_path)[:5] == ["pr", "comment", "12", "--repo", "o/r"]


def test_diff_hostile_inputs_reach_the_cli_as_plain_arguments(tmp_path: Path):
    canary = tmp_path / "pwned"
    hostile = f'-x"; touch {canary}; echo "'
    _fake(tmp_path, "uv", RECORD_ARGV)
    assert _run(DIFF_STEP, _env(tmp_path, DIFF_STEP, lambda _: hostile)) == 0
    argv = _argv(tmp_path)
    assert not canary.exists()
    assert argv[argv.index("--cases") + 1] == hostile
    # A flag input that is not "true" adds nothing, and a path can't become an option.
    assert "--intersect" not in argv and "--fail-on-regression" not in argv
    assert argv[-3:] == ["--", hostile, hostile]


def test_diff_writes_the_summary_and_keeps_the_exit_code(tmp_path: Path):
    _fake(tmp_path, "uv", f"{RECORD_ARGV}\necho 'Verdict: B is a regression.'\nexit 1")
    inputs = {"intersect": "true", "fail-on-regression": "true", "cases": ""}
    env = _env(tmp_path, DIFF_STEP, lambda name: inputs.get(name, f"{name}.jsonl"))
    assert _run(DIFF_STEP, env) == 1
    assert "B is a regression" in (tmp_path / "summary").read_text()
    argv = _argv(tmp_path)
    assert {"--intersect", "--fail-on-regression"} <= set(argv) and "--cases" not in argv
    assert argv[-2:] == ["baseline.jsonl", "candidate.jsonl"]


def test_workflow_permissions_stay_narrow():
    triage = WORKFLOWS["triage"]
    assert triage["permissions"] == {"contents": "read", "actions": "read"}
    # PyYAML reads the bare key `on` as True.
    assert triage[True] == {"workflow_run": {"workflows": ["ci"], "types": ["completed"]}}
    release = WORKFLOWS["release"]
    assert release["permissions"] == {"contents": "read"}
    assert "permissions" not in release["jobs"]["build"]
    publish = release["jobs"]["publish"]
    assert publish["permissions"] == {"id-token": "write"}
    assert publish["environment"]["name"] == "pypi"
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "secrets." not in text and "password" not in text
