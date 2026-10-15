"""Claude Code as the harness: a recorded headless run, mapped onto the same grader.

The fixture is a scrubbed `claude -p --output-format stream-json` run of scenario
005 where the operator denied the rollback.
"""

import json
from pathlib import Path

from conftest import SCENARIOS

from rootcause.adapters import claude_code
from rootcause.adapters.claude_code import (
    ClaudeCodeHarness,
    allowed_tools,
    command,
    parse_stream,
    stage_case,
)
from rootcause.adapters.fixtures import load_case
from rootcause.adapters.mcp_telemetry import build_telemetry_server
from rootcause.adapters.mcp_tools import mcp_tools
from rootcause.adapters.sqlite_events import SqliteEventLog
from rootcause.application.evals import grade
from rootcause.composition import Mode, investigate, supports
from rootcause.domain.answers import Outcome
from rootcause.domain.scenario import Category

FIXTURE = Path(__file__).parent / "fixtures" / "claude_code_005_denied_rollback.jsonl"
CASE_005 = SCENARIOS / "005-inventory-bad-deploy"
GATED = frozenset({"rollback_deploy"})


def lines() -> list[str]:
    return FIXTURE.read_text().splitlines()


def test_stream_maps_to_a_gradable_run():
    result = parse_stream(lines(), run_id="r1", gated=GATED, approve=False)
    assert result.outcome is Outcome.ANSWERED
    assert result.answer is not None
    assert result.answer.category is Category.BAD_CODE_DEPLOY
    names = [c.name for c in result.tool_calls]
    assert names[0] == "query_logs"
    assert "StructuredOutput" not in names
    rollback = next(c for c in result.tool_calls if c.name == "rollback_deploy")
    assert (rollback.gated, rollback.approved, rollback.is_error) == (True, False, True)
    reads = [c for c in result.tool_calls if c.name != "rollback_deploy"]
    assert all(not c.gated and not c.is_error for c in reads)
    graded = grade(
        load_case(CASE_005),
        result,
        provider="claude-code",
        mode="single",
        tool_calls=len(result.tool_calls),
    )
    assert graded.unapproved_actions == 0
    assert graded.passed


def result_line(subtype: str, structured: object = None) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": subtype,
            "duration_ms": 10,
            "total_cost_usd": 0.01,
            "permission_denials": [],
            "structured_output": structured,
        }
    )


def test_harness_limits_map_to_outcomes():
    def outcome(line: str) -> Outcome:
        return parse_stream([line], run_id="r", gated=GATED, approve=False).outcome

    assert outcome(result_line("error_max_turns")) is Outcome.STEP_LIMIT
    assert outcome(result_line("error_max_budget_usd")) is Outcome.BUDGET_EXCEEDED
    assert outcome(result_line("success", {"category": "not-a-category"})) is Outcome.NO_ANSWER
    assert parse_stream([], run_id="r", gated=GATED, approve=False).outcome is Outcome.ERROR


def test_command_offers_only_the_telemetry_server(tmp_path):
    cmd = command(
        mcp_config=tmp_path / "mcp.json",
        allowed=["query_logs", "list_deploys"],
        model="claude-opus-5-5",
        effort="high",
        max_turns=15,
        budget_usd=0.5,
    )
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == (
        "mcp__telemetry__query_logs,mcp__telemetry__list_deploys"
    )
    assert cmd[cmd.index("--max-turns") + 1] == "15"


async def test_operator_decides_whether_rollback_is_allowed():
    case = load_case(CASE_005)
    async with mcp_tools(build_telemetry_server(case.store)) as tools:
        denied = allowed_tools(tools, approve=False)
        approved = allowed_tools(tools, approve=True)
    assert "rollback_deploy" not in denied
    assert "query_logs" in denied
    assert set(approved) - set(denied) == {"rollback_deploy"}


def test_staged_fixtures_never_include_the_answer(tmp_path):
    case = load_case(CASE_005)
    staged = load_case(stage_case(case, tmp_path))
    assert staged.scenario.truth is None
    assert staged.store.evidence_ids() == case.store.evidence_ids()


def test_claude_code_is_single_agent_only():
    assert supports("claude-code:claude-opus-5-5:high", Mode.SINGLE)
    assert not supports("claude-code:claude-opus-5-5:high", Mode.MULTI)


async def test_investigate_grades_a_claude_code_run():
    seen: list[list[str]] = []

    async def spawn(cmd: list[str], cwd: Path) -> list[str]:
        seen.append(cmd)
        assert (cwd / "scenario.yaml").exists() is False  # the MCP fixtures live elsewhere
        return lines()

    events = SqliteEventLog()
    run = await investigate(
        load_case(CASE_005),
        ClaudeCodeHarness(spawn=spawn),
        events=events,
        budget_usd=0.5,
        approve=False,
    )
    assert run.grade.passed
    assert run.grade.provider == "claude-code:claude-opus-5-5:high"
    assert run.grade.tool_calls == len(run.result.tool_calls)
    assert "mcp__telemetry__rollback_deploy" not in seen[0][seen[0].index("--allowedTools") + 1]
    assert events.run_info(run.result.run_id).provider == "claude-code:claude-opus-5-5:high"


def test_an_approved_rollback_id_counts_as_returned_evidence():
    """The rollback tool returns an action id, so citing it is not inventing evidence.
    Found when Claude Code cited rb-d-2101 on scenario 021 and was graded as inventing it."""
    case = load_case(CASE_005)
    denied = parse_stream(lines(), run_id="r", gated=GATED, approve=False)
    assert denied.answer is not None
    cited = denied.answer.model_copy(update={"evidence": [*denied.answer.evidence, "rb-d-5001"]})
    calls = [
        c.model_copy(update={"approved": True, "is_error": False})
        if c.name == "rollback_deploy"
        else c
        for c in denied.tool_calls
    ]
    approved = denied.model_copy(update={"answer": cited, "tool_calls": calls})

    def graded(result):
        return grade(case, result, provider="p", mode="single", tool_calls=len(calls))

    assert graded(approved).invented_evidence == []
    assert graded(approved).passed
    # A denied rollback returned no action id, so citing one is still invented.
    assert graded(denied.model_copy(update={"answer": cited})).invented_evidence == ["rb-d-5001"]


def parse(raw: list[str], approve: bool = False):
    return parse_stream(raw, run_id="r", gated=GATED, approve=approve)


def test_a_truncated_line_is_an_error_not_a_crash():
    result = parse([*lines()[:3], '{"type": "assist'])
    assert result.outcome is Outcome.ERROR
    assert "unparseable" in result.note


def test_a_harness_failure_is_not_blamed_on_the_model():
    line = json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "401"})
    result = parse([line])
    assert result.outcome is Outcome.ERROR
    assert "401" in result.note


def test_an_unapproved_rollback_is_never_marked_approved():
    """Cut off after the rollback call, before any denial arrived."""
    raw = lines()
    cut = raw[: next(i for i, line in enumerate(raw) if "rollback_deploy" in line) + 1]
    rollback = next(c for c in parse(cut).tool_calls if c.name == "rollback_deploy")
    assert rollback.approved is False
    approved = next(c for c in parse(cut, approve=True).tool_calls if c.name == "rollback_deploy")
    assert approved.approved is True


async def test_spawn_reports_exit_code_and_stderr(tmp_path):
    out = await claude_code.spawn_claude(
        ["sh", "-c", 'echo \'{"type": "x"}\'; echo bad login >&2; exit 3'], tmp_path
    )
    result = parse(out)
    assert result.outcome is Outcome.ERROR
    assert "exit 3" in result.note and "bad login" in result.note


async def test_spawn_keeps_partial_output_and_kills_on_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_code, "TIMEOUT_S", 2.0)  # room for a slow sh start
    out = await claude_code.spawn_claude(
        ["sh", "-c", 'echo \'{"type": "x"}\'; exec sleep 30'], tmp_path
    )
    assert json.loads(out[0]) == {"type": "x"}
    assert "timed out" in parse(out).note


async def test_a_missing_claude_binary_is_an_error_not_a_crash(tmp_path):
    out = await claude_code.spawn_claude(["rootcause-no-such-binary"], tmp_path)
    result = parse(out)
    assert result.outcome is Outcome.ERROR
    assert "could not start" in result.note


def test_stream_usage_and_served_model_are_kept():
    result = parse(lines())
    assert result.models == ["claude-opus-5-5"]
    assert result.usage.output_tokens == 1117
    assert result.usage.cache_read_tokens == 15386
    assert result.usage.cache_write_tokens == 3662
