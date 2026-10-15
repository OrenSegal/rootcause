"""The two ways in from outside: the CLI that CI calls, and the MCP server coding agents call."""

import json

from conftest import SCENARIOS
from mcp import Client
from typer.testing import CliRunner

from rootcause.adapters.heuristic import HeuristicProvider
from rootcause.adapters.sqlite_events import SqliteEventLog
from rootcause.interfaces.cli import app
from rootcause.interfaces.mcp_server import build_agent_server

# GitHub Actions makes Rich emit color and wrap at its own width, which splits
# the messages these tests look for. Pin both so output matches a local run.
runner = CliRunner(
    env={"NO_COLOR": "1", "FORCE_COLOR": None, "GITHUB_ACTIONS": None, "COLUMNS": "200"}
)


def test_investigate_json_and_exit_code(scenario_dir, tmp_path):
    out = runner.invoke(
        app, ["investigate", str(scenario_dir), "--json", "--runlog", str(tmp_path / "r.sqlite")]
    )
    assert out.exit_code == 0, out.output
    payload = json.loads(out.output)
    assert payload["grade"]["passed"] is True
    assert payload["result"]["answer"]["category"] == "config_change"


def test_eval_gate_writes_summary_and_fails_below_threshold(scenario_dir, tmp_path):
    summary = tmp_path / "summary.md"
    report = tmp_path / "report.json"
    one = tmp_path / "one"
    one.mkdir()
    (one / scenario_dir.name).symlink_to(scenario_dir)
    args = ["eval", str(one), "--runlog", str(tmp_path / "r.sqlite")]
    ok = runner.invoke(app, [*args, "--summary-md", str(summary), "--json-out", str(report)])
    assert ok.exit_code == 0, ok.output
    assert "Pass rate **100%**" in summary.read_text()
    assert json.loads(report.read_text())["pass_rate"] == 1.0

    # An impossible bar must fail the job.
    strict = runner.invoke(app, [*args, "--min-pass-rate", "1.01"])
    assert strict.exit_code == 1


async def test_agent_server_runs_an_investigation(scenario_dir):
    server = build_agent_server(
        scenario_dir.parent, HeuristicProvider, SqliteEventLog(), budget_usd=1.0
    )
    async with Client(server) as client:
        listing = await client.call_tool("list_scenarios", {})
        run = await client.call_tool("investigate_incident", {"scenario_id": scenario_dir.name})
        missing = await client.call_tool("investigate_incident", {"scenario_id": "nope"})
    assert listing.structured_content is not None
    ids = [s["id"] for s in listing.structured_content["result"]]
    assert scenario_dir.name in ids
    assert run.structured_content is not None
    assert run.structured_content["grade"]["passed"] is True
    assert missing.is_error


def test_ci_triage_from_recorded_raw_writes_a_pr_comment(tmp_path):
    raw = SCENARIOS / "008-sous-shellcheck" / "raw"
    comment = tmp_path / "comment.md"
    out = runner.invoke(
        app,
        [
            "ci-triage",
            "--raw", str(raw),
            "--comment-md", str(comment),
            "--runlog", str(tmp_path / "r.sqlite"),
        ],
    )  # fmt: skip
    assert out.exit_code == 0, out.output
    text = comment.read_text()
    assert text.startswith("### rootcause: likely cause of this CI failure")
    assert "4870121d6d" in text and "heuristic" in text


def test_ci_triage_needs_a_run_or_raw_input(tmp_path):
    out = runner.invoke(app, ["ci-triage", "--runlog", str(tmp_path / "r.sqlite")])
    assert out.exit_code != 0
    assert "--repo and --run, or --raw" in out.output
