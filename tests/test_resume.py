"""A run that crashes can be resumed from its event log: same history, no tool run
twice, and a side effect of unknown outcome reported instead of repeated."""

from dataclasses import dataclass
from typing import Any

import pytest
from conftest import Scripted, call, make_loop

from rootcause.adapters.sqlite_events import SqliteEventLog
from rootcause.application.investigator import investigator_spec
from rootcause.application.ports import ToolResult
from rootcause.domain.answers import Outcome

DIAGNOSIS = {
    "category": "config_change",
    "service": "checkout",
    "summary": "x",
    "evidence": ["d-1042"],
    "recommended_action": "x",
    "confidence": 0.5,
}


class Crash(BaseException):
    """Stands in for the process dying: not an Exception, so nothing catches it."""


@dataclass
class CountingTool:
    name: str
    read_only: bool = True
    idempotent: bool = True
    crash_after_effect: bool = False
    calls: int = 0

    @property
    def definition(self) -> dict[str, Any]:
        return {"name": self.name, "description": "", "input_schema": {"type": "object"}}

    async def invoke(self, args: dict[str, Any]) -> ToolResult:
        self.calls += 1
        if self.crash_after_effect and self.calls == 1:
            raise Crash
        return ToolResult(f"{self.name} ok")


def spec():
    return investigator_spec(max_steps=10)


async def test_resume_after_a_crash_in_the_model_call_replays_the_same_history():
    events = SqliteEventLog()
    logs = CountingTool("query_logs")
    first = Scripted(
        [
            {"content": [call("query_logs", {}, "a")]},
            {"content": [call("query_logs", {}, "b")]},
            Crash(),
        ]
    )
    with pytest.raises(Crash):
        await make_loop(first, events=events).run(spec(), "ALERT", [logs], scenario_id="s")
    (run_id,) = [r for r in _run_ids(events)]

    second = Scripted([{"content": [call("submit_diagnosis", DIAGNOSIS, "c")]}])
    result = await make_loop(second, events=events).resume(run_id, spec(), [logs])

    assert result.outcome is Outcome.ANSWERED
    assert second.seen[0] == first.seen[2]  # identical history at the point of the crash
    assert logs.calls == 2  # nothing re-run
    assert result.steps == 3


async def test_crash_between_side_effect_and_log_is_not_repeated():
    events = SqliteEventLog()
    page = CountingTool("page_oncall", read_only=False, idempotent=False, crash_after_effect=True)
    first = Scripted([{"content": [call("page_oncall", {}, "a")]}])
    with pytest.raises(Crash):
        await make_loop(first, approve=True, events=events).run(
            spec(), "ALERT", [page], scenario_id="s"
        )
    (run_id,) = _run_ids(events)

    second = Scripted([{"content": [call("submit_diagnosis", DIAGNOSIS, "b")]}])
    result = await make_loop(second, approve=True, events=events).resume(run_id, spec(), [page])

    assert page.calls == 1  # at most once
    unknown = second.seen[0][-1]["content"][0]
    assert unknown["is_error"] and "outcome is unknown" in unknown["content"]
    assert result.outcome is Outcome.ANSWERED


async def test_tools_that_finished_before_a_mid_turn_crash_are_not_rerun():
    events = SqliteEventLog()
    logs = CountingTool("query_logs")
    page = CountingTool("page_oncall", read_only=False, idempotent=False, crash_after_effect=True)
    first = Scripted([{"content": [call("query_logs", {}, "a"), call("page_oncall", {}, "b")]}])
    with pytest.raises(Crash):
        await make_loop(first, approve=True, events=events).run(
            spec(), "ALERT", [logs, page], scenario_id="s"
        )
    (run_id,) = _run_ids(events)

    second = Scripted([{"content": [call("submit_diagnosis", DIAGNOSIS, "c")]}])
    await make_loop(second, approve=True, events=events).resume(run_id, spec(), [logs, page])

    assert logs.calls == 1 and page.calls == 1
    reused, unknown = second.seen[0][-1]["content"]
    assert reused["content"] == "query_logs ok"
    assert "outcome is unknown" in unknown["content"]


async def test_crash_before_an_idempotent_tool_finishes_reruns_it():
    events = SqliteEventLog()
    flaky = CountingTool("query_logs", crash_after_effect=True)
    first = Scripted([{"content": [call("query_logs", {}, "a")]}])
    with pytest.raises(Crash):
        await make_loop(first, events=events).run(spec(), "ALERT", [flaky], scenario_id="s")
    (run_id,) = _run_ids(events)

    second = Scripted([{"content": [call("submit_diagnosis", DIAGNOSIS, "b")]}])
    await make_loop(second, events=events).resume(run_id, spec(), [flaky])

    assert flaky.calls == 2  # read-only: safe to run again
    assert second.seen[0][-1]["content"][0]["content"] == "query_logs ok"


async def test_resumed_run_counts_what_was_already_spent():
    events = SqliteEventLog()
    tool = CountingTool("query_logs")
    turn = {
        "content": [call("query_logs", {}, "a")],
        "usage": {"input_tokens": 0, "output_tokens": 30_000},
    }
    first = Scripted([turn, Crash()])  # $0.60 on Opus 5.5, then crash
    with pytest.raises(Crash):
        await make_loop(first, events=events, budget=1.0).run(spec(), "A", [tool], scenario_id="s")
    (run_id,) = _run_ids(events)

    second = Scripted([turn, turn])
    result = await make_loop(second, events=events, budget=1.0).resume(run_id, spec(), [tool])
    assert result.outcome is Outcome.BUDGET_EXCEEDED
    assert len(second.seen) == 1


async def test_a_finished_run_cannot_be_resumed():
    events = SqliteEventLog()
    done = Scripted([{"content": [call("submit_diagnosis", DIAGNOSIS, "a")]}])
    result = await make_loop(done, events=events).run(spec(), "A", [], scenario_id="s")
    with pytest.raises(ValueError, match="already finished"):
        await make_loop(done, events=events).resume(result.run_id, spec(), [])


def _run_ids(events: SqliteEventLog) -> list[str]:
    return [row[0] for row in events.db.execute("select run_id from runs").fetchall()]
