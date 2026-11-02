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
