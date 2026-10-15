"""Delivery semantics of tool calls: retries, timeouts, and what is never retried."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from rootcause.adapters.sqlite_events import SqliteEventLog
from rootcause.application.approval import StaticApproval
from rootcause.application.executor import ExecSettings, ToolExecutor
from rootcause.application.investigator import SUBMIT_DIAGNOSIS
from rootcause.application.ports import ToolResult

FAST = ExecSettings(timeout_s=0.05, max_attempts=3, backoff_s=0)


@dataclass
class FakeTool:
    name: str = "probe"
    read_only: bool = True
    idempotent: bool = True
    failures: int = 0
    hang: bool = False
    calls: int = field(default=0)

    @property
    def definition(self) -> dict[str, Any]:
        return {"name": self.name, "description": "", "input_schema": {"type": "object"}}

    async def invoke(self, args: dict[str, Any]) -> ToolResult:
        self.calls += 1
        if self.hang:
            await asyncio.sleep(1)
        if self.calls <= self.failures:
            raise ConnectionError("flaky")
        return ToolResult("ok")


def executor(tool: FakeTool, approve: bool = True) -> tuple[ToolExecutor, SqliteEventLog, str]:
    events = SqliteEventLog()
    run_id = events.start_run(scenario_id="s", agent="a", provider="p")
    return (
        ToolExecutor(
            tools={tool.name: tool},
            terminal=SUBMIT_DIAGNOSIS,
            approval=StaticApproval(approve),
            events=events,
            settings=FAST,
        ),
        events,
        run_id,
    )


def use(name: str = "probe") -> dict[str, Any]:
    return {"type": "tool_use", "id": "u1", "name": name, "input": {}}


async def test_idempotent_tool_is_retried_until_it_succeeds():
    tool = FakeTool(failures=2)
    ex, _, run_id = executor(tool)
    out = await ex.execute(run_id, 1, use())
    assert out.result["is_error"] is False
    assert tool.calls == 3 and out.call and out.call.attempts == 3


async def test_retries_stop_at_the_limit():
    tool = FakeTool(failures=5)
    ex, _, run_id = executor(tool)
    out = await ex.execute(run_id, 1, use())
    assert out.result["is_error"] is True and "after 3 attempt" in out.result["content"]
    assert tool.calls == 3


async def test_non_idempotent_tool_is_never_retried():
    tool = FakeTool(read_only=False, idempotent=False, failures=1)
    ex, _, run_id = executor(tool)
    out = await ex.execute(run_id, 1, use())
    assert out.result["is_error"] is True
    assert tool.calls == 1


async def test_a_hanging_tool_times_out():
    tool = FakeTool(hang=True)
    ex, _, run_id = executor(tool)
    out = await ex.execute(run_id, 1, use())
    assert out.result["is_error"] is True and "TimeoutError" in out.result["content"]


async def test_intent_is_logged_before_a_side_effect_runs():
    tool = FakeTool(read_only=False, idempotent=False)
    ex, events, run_id = executor(tool, approve=True)
    await ex.execute(run_id, 1, use())
    kinds = [e.kind for e in events.events(run_id)]
    assert kinds == ["approval", "tool_intent", "tool_call"]


async def test_unknown_tool_is_an_error_result_not_an_exception():
    ex, _, run_id = executor(FakeTool())
    out = await ex.execute(run_id, 1, use("nope"))
    assert out.result["is_error"] is True and out.call is None
