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


def test_investigate_prints_why_a_run_errored(scenario_dir, tmp_path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    args = ["investigate", str(scenario_dir), "--provider", "anthropic"]
    out = runner.invoke(app, [*args, "--runlog", str(tmp_path / "r.sqlite")])
    assert out.exit_code == 1, out.output
    assert "note: model call failed" in out.output


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


def test_eval_and_compare_refuse_an_empty_scenarios_dir(tmp_path):
    for command in ("eval", "compare"):
        out = runner.invoke(app, [command, str(tmp_path), "--runlog", str(tmp_path / "r.sqlite")])
        assert out.exit_code == 2, out.output
        assert "no scenarios in" in out.output


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


async def test_agent_server_only_investigates_scenarios_under_its_root(scenario_dir):
    """The id comes from the calling agent, so it must name a directory the server was
    given, not a path out of it."""
    external = scenario_dir.parent.parent / "scenarios-external" / "x01-waf-regex-backtracking"
    server = build_agent_server(
        scenario_dir.parent, HeuristicProvider, SqliteEventLog(), budget_usd=1.0
    )
    async with Client(server) as client:
        for outside in (f"../scenarios-external/{external.name}", str(external), "."):
            run = await client.call_tool("investigate_incident", {"scenario_id": outside})
            assert run.is_error, outside


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
    assert text.startswith("### CI failure triage")
    assert "4870121d6d" in text and "heuristic provider" in text


def _triage(raw, tmp_path, *extra: str):
    comment = tmp_path / "comment.md"
    out = runner.invoke(
        app,
        [
            "ci-triage", "--raw", str(raw), "--provider", "rules",
            "--comment-md", str(comment), "--runlog", str(tmp_path / "r.sqlite"), *extra,
        ],
    )  # fmt: skip
    return out, comment.read_text()


def test_ci_triage_rules_abstain_plainly_when_no_rule_fires(tmp_path):
    """The recorded 008 log has no failure line that names a changed file, and no
    environment error, so no rule fires. The verdict says so instead of guessing."""
    out, text = _triage(SCENARIOS / "008-sous-shellcheck" / "raw", tmp_path)
    assert out.exit_code == 2, out.output
    assert "Job `test (ubuntu-latest)` failed at step `shellcheck`." in text
    assert "No rule fired; this needs a person or a model." in text
    assert "rules provider, no rule fired" in text
    assert "caused it" not in text and "4870121d6d" not in text


def test_ci_triage_rules_name_the_commit_and_the_log_lines(tmp_path):
    """A live-shaped run through `--raw`: the recorded 008 run and commits, with a
    synthetic failed log whose error line names a file only commit 4870121d6d changed.
    The line also carries a code fence and a mention, which must not reach a comment
    intact."""
    recorded = SCENARIOS / "008-sous-shellcheck" / "raw"
    raw = tmp_path / "raw"
    raw.mkdir()
    for name in ("run.json", "commits.json"):
        (raw / name).write_text((recorded / name).read_text())
    line = "tests/gate.test.sh:51: error: green breaks ``` @octocat"
    (raw / "failed.log").write_text(
        f"test (ubuntu-latest)\tshellcheck\t2026-10-02T03:30:08.0225201Z {line}\n"
    )
    out, text = _triage(raw, tmp_path)
    assert out.exit_code == 0, out.output
    sentences, _, rest = text.partition("The log lines behind it:")
    assert "The change under test likely caused it: commit `4870121d6d`" in sentences
    assert "tests/gate.test.sh:51: error" in rest
    assert "```" not in rest.split("```text", 1)[1].rsplit("```", 1)[0]
    assert "@octocat" not in text
    assert "rules provider, rule one_commit_named" in text


def test_ci_triage_needs_a_run_or_raw_input(tmp_path):
    out = runner.invoke(app, ["ci-triage", "--runlog", str(tmp_path / "r.sqlite")])
    assert out.exit_code != 0
    assert "--repo and --run, or --raw" in out.output
