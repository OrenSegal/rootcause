"""The hand-written loop on a live model through Claude Code: each turn is one headless
`claude -p` call that returns the next tool calls as structured output."""

import json

import pytest
from conftest import SCENARIOS

from rootcause.adapters.claude_code_completion import ClaudeCodeCompletion, CompletionError
from rootcause.adapters.fixtures import load_case
from rootcause.adapters.sqlite_events import SqliteEventLog
from rootcause.composition import investigate
from rootcause.domain.answers import Outcome

TOOLS = [
    {"name": "list_deploys", "description": "List deploys.", "input_schema": {"type": "object"}},
    {"name": "submit_diagnosis", "description": "Report.", "input_schema": {"type": "object"}},
]
DIAGNOSIS = {
    "category": "config_change", "service": "checkout", "summary": "s",
    "evidence": ["d-1042"], "recommended_action": "a", "confidence": 0.8,
}  # fmt: skip


def result(calls, model="claude-opus-5-5", **extra):
    return json.dumps(
        {
            "type": "result", "subtype": "success", "is_error": False,
            "structured_output": {"text": "next", "tool_calls": calls},
            "usage": {"input_tokens": 10, "output_tokens": 20,
                      "cache_read_input_tokens": 300, "cache_creation_input_tokens": 40},
            "modelUsage": {model: {}}, "duration_ms": 1200, **extra,
        }
    )  # fmt: skip


def call(name, args):
    return {"name": name, "input_json": json.dumps(args)}


class Replay:
    def __init__(self, outputs):
        self.outputs, self.prompts = list(outputs), []

    async def __call__(self, cmd, stdin):
        self.prompts.append((cmd, stdin))
        return self.outputs.pop(0)


async def test_a_completion_becomes_tool_use_blocks_with_usage_and_served_model():
    replay = Replay([result([call("list_deploys", {}), call("submit_diagnosis", DIAGNOSIS)])])
    provider = ClaudeCodeCompletion(spawn=replay)
    msg = await provider.complete(
        system=[{"type": "text", "text": "brief"}],
        messages=[{"role": "user", "content": "ALERT"}],
        tools=TOOLS,
    )
    uses = [b for b in msg.content if b.type == "tool_use"]
    assert [u.name for u in uses] == ["list_deploys", "submit_diagnosis"]
    assert uses[1].input == DIAGNOSIS
    assert len({u.id for u in uses}) == 2
    assert msg.stop_reason == "tool_use"
    assert msg.model == "claude-opus-5-5"
    assert msg.usage.cache_read_input_tokens == 300
    cmd, stdin = replay.prompts[0]
    assert "list_deploys" in cmd[cmd.index("--system-prompt") + 1]
    assert "ALERT" in stdin
    assert cmd[cmd.index("--tools") + 1] == ""


async def test_tool_results_are_rendered_into_the_next_prompt():
    replay = Replay([result([])])
    await ClaudeCodeCompletion(spawn=replay).complete(
        system=[{"type": "text", "text": "brief"}],
        messages=[
            {"role": "user", "content": "ALERT"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "list_deploys", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": '[{"id": "d-1042"}]',
                        "is_error": False,
                    }
                ],
            },
        ],
        tools=TOOLS,
    )
    stdin = replay.prompts[0][1]
    assert "list_deploys" in stdin and "d-1042" in stdin and "t1" in stdin


async def test_a_failed_completion_raises_a_typed_error():
    bad = json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "429"})
    with pytest.raises(CompletionError, match="429"):
        await ClaudeCodeCompletion(spawn=Replay([bad])).complete(
            system=[], messages=[{"role": "user", "content": "x"}], tools=TOOLS
        )


async def test_the_hand_written_loop_runs_and_grades_through_claude_code(case):
    replay = Replay(
        [
            result([call("list_deploys", {})]),
            result([call("submit_diagnosis", DIAGNOSIS)]),
        ]
    )
    run = await investigate(
        case, ClaudeCodeCompletion(spawn=replay), events=SqliteEventLog(), budget_usd=1.0
    )
    assert run.result.outcome is Outcome.ANSWERED
    assert run.grade.passed
    assert run.grade.models == ["claude-opus-5-5"]
    assert run.grade.provider == "claude-code-loop:claude-opus-5-5:high"


async def test_a_provider_failure_ends_the_run_as_a_harness_error(case):
    bad = json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "boom"})
    run = await investigate(
        case, ClaudeCodeCompletion(spawn=Replay([bad])), events=SqliteEventLog(), budget_usd=1.0
    )
    assert run.result.outcome is Outcome.ERROR
    assert "boom" in run.result.note
    assert run.grade.status == "error"


def test_scenario_constant_resolves():
    assert load_case(SCENARIOS / "001-checkout-pool-config").scenario.id
