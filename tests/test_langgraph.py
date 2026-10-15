"""The same investigation built on LangGraph: graph-owned control flow, the same
executor, briefs, approval policy and grader. Skipped without the langgraph extra."""

import pytest
from conftest import Scripted, call
from test_orchestrator import FINDINGS, Router, dispatch

pytest.importorskip("langgraph")

from rootcause.adapters.langgraph_engine import LangGraphEngine
from rootcause.adapters.sqlite_events import SqliteEventLog
from rootcause.composition import Mode, investigate
from rootcause.domain.answers import Outcome

DIAGNOSIS = {
    "category": "config_change", "service": "checkout", "summary": "s",
    "evidence": ["d-1042"], "recommended_action": "a", "confidence": 0.8,
}  # fmt: skip


async def run(case, provider, **kw):
    events = SqliteEventLog()
    result = await investigate(case, LangGraphEngine(provider), events=events, budget_usd=1.0, **kw)
    return result, events


async def test_single_agent_graph_investigates_and_is_graded(case):
    provider = Scripted(
        [
            {"content": [call("list_deploys", {}, "a"),
                         call("query_metrics", {"service": "checkout"}, "b")]},
            {"content": [call("submit_diagnosis", DIAGNOSIS, "c")]},
        ]
    )  # fmt: skip
    run_, _ = await run(case, provider)
    assert run_.result.outcome is Outcome.ANSWERED
    assert run_.grade.passed
    assert run_.grade.provider == "langgraph:scripted"
    assert [c.name for c in run_.result.tool_calls] == ["list_deploys", "query_metrics"]
    # both results went back in one message, as with the hand-written loop
    assert [b["tool_use_id"] for b in provider.seen[1][-1]["content"]] == ["a", "b"]
    assert run_.grade.tool_calls == 2


async def test_rollback_goes_through_the_same_approval_gate(case):
    provider = Scripted(
        [
            {"content": [call("rollback_deploy", {"deploy_id": "d-1042", "reason": "x"}, "a"),
                         call("list_deploys", {}, "b")]},
            {"content": [call("submit_diagnosis", DIAGNOSIS, "c")]},
        ]
    )  # fmt: skip
    run_, _ = await run(case, provider, approve=False)
    rollback = run_.result.tool_calls[0]
    assert rollback.gated and rollback.approved is False
    assert case.store.rollbacks == {}


async def test_nudge_then_no_answer_and_step_limit(case):
    silent = Scripted(
        [{"content": [{"type": "text", "text": "thinking"}], "stop_reason": "end_turn"}] * 2
    )
    run_, _ = await run(case, silent)
    assert run_.result.outcome is Outcome.NO_ANSWER
    looping = Scripted([{"content": [call("list_deploys", {}, f"x{i}")]} for i in range(20)])
    run_, _ = await run(case, looping)
    assert run_.result.outcome is Outcome.STEP_LIMIT
    assert run_.result.steps == 15


async def test_budget_stops_the_graph(case):
    pricey = {"input_tokens": 200_000, "output_tokens": 20_000}
    provider = Scripted(
        [{"content": [call("list_deploys", {}, f"a{i}")], "usage": pricey} for i in range(3)]
    )
    run_, _ = await run(case, provider)
    assert run_.result.outcome is Outcome.BUDGET_EXCEEDED
    assert len(provider.seen) == 1


async def test_multi_agent_fans_out_workers_with_send_and_joins_their_findings(case):
    orchestrator = Scripted(
        [
            {"content": [dispatch("changes", "logs")]},
            {"content": [call("submit_diagnosis",
                              {**DIAGNOSIS, "evidence": ["cfg:d-1042:DB_POOL_MAX"]}, "s")]},
        ]
    )  # fmt: skip
    changes = Scripted(
        [
            {"content": [call("diff_config", {"deploy_id": "d-1042"}, "c1")]},
            {"content": [call("submit_findings", FINDINGS, "c2")]},
        ]
    )
    logs = Scripted(
        [
            {"content": [call("query_logs", {"service": "checkout", "level": "error"}, "l0")]},
            {"content": [call("submit_findings", {**FINDINGS, "evidence": ["log-0013"]}, "l1")]},
        ]
    )
    run_, events = await run(
        case, Router(orchestrator=orchestrator, changes=changes, logs=logs), mode=Mode.MULTI
    )
    assert run_.result.outcome is Outcome.ANSWERED, run_.result.note
    children = events.children(run_.result.run_id)
    assert sorted(c.agent for c in children) == ["worker:changes", "worker:logs"]
    joined = orchestrator.seen[1][-1]["content"][0]
    assert joined["tool_use_id"] == "d" and "cfg:d-1042:DB_POOL_MAX" in joined["content"]
    assert run_.grade.tool_calls == 2  # the workers' telemetry calls
