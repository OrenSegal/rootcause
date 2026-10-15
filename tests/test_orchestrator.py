"""Orchestrator-worker: the orchestrator plans and decides, workers investigate in
parallel with read-only tool subsets, everyone spends from one budget, and every
worker run is linked to its parent in the event log."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from conftest import Scripted, call, make_loop

from rootcause.adapters.mcp_telemetry import build_telemetry_server
from rootcause.adapters.mcp_tools import mcp_tools
from rootcause.adapters.sqlite_events import SqliteEventLog
from rootcause.application.orchestrator import (
    DispatchWorkers,
    WorkerRole,
    orchestrator_spec,
    worker_spec,
)
from rootcause.application.ports import ToolResult
from rootcause.domain.answers import Outcome

FINDINGS = {
    "summary": "d-1042 cut DB_POOL_MAX to 5 two minutes before errors.",
    "evidence": ["cfg:d-1042:DB_POOL_MAX"],
    "suspected_service": "checkout",
    "confidence": 0.8,
}
DIAGNOSIS = {
    "category": "config_change",
    "service": "checkout",
    "summary": "Pool cut to 5.",
    "evidence": ["cfg:d-1042:DB_POOL_MAX"],
    "recommended_action": "Restore DB_POOL_MAX=50.",
    "confidence": 0.85,
}


class Router:
    """One provider shared by every agent in the loop; routes by system prompt."""

    name = "router"

    def __init__(self, **by_agent: Scripted) -> None:
        self.by_agent = by_agent

    async def complete(self, *, system, messages, tools):
        text = system[0]["text"]
        for key, provider in self.by_agent.items():
            if text.startswith(f"[{key}]"):
                return await provider.complete(system=system, messages=messages, tools=tools)
        raise AssertionError(f"no script for prompt {text[:40]!r}")


def dispatch(*roles: str, id_: str = "d") -> dict[str, Any]:
    tasks = [{"role": r, "question": f"What do the {r} show?"} for r in roles]
    return call("dispatch_workers", {"tasks": tasks}, id_)


def test_workers_see_only_their_read_only_tools(store):
    names = {
        "query_logs", "query_metrics", "search_traces", "get_trace",
        "list_deploys", "diff_config", "rollback_deploy",
    }  # fmt: skip

    @dataclass
    class T:
        name: str

        @property
        def read_only(self) -> bool:
            return self.name != "rollback_deploy"

    tools = [T(n) for n in sorted(names)]
    seen = {role: {t.name for t in worker_spec(role).select(tools)} for role in WorkerRole}
    assert seen[WorkerRole.CHANGES] == {"list_deploys", "diff_config"}
    assert seen[WorkerRole.LOGS] == {"query_logs", "search_traces", "get_trace"}
    assert seen[WorkerRole.METRICS] == {"query_metrics"}
    assert all("rollback_deploy" not in s for s in seen.values())


async def test_orchestrator_dispatches_workers_and_diagnoses(case):
    orchestrator = Scripted(
        [
            {"content": [dispatch("changes", "logs")]},
            {"content": [call("submit_diagnosis", DIAGNOSIS, "s")]},
        ]
    )
    changes = Scripted(
        [
            {"content": [call("diff_config", {"deploy_id": "d-1042"}, "c1")]},
            {"content": [call("submit_findings", FINDINGS, "c2")]},
        ]
    )
    logs = Scripted(
        [{"content": [call("submit_findings", {**FINDINGS, "evidence": ["log-0013"]}, "l1")]}]
    )
    events = SqliteEventLog()
    loop = make_loop(Router(orchestrator=orchestrator, changes=changes, logs=logs), events=events)

    async with mcp_tools(build_telemetry_server(case.store)) as tools:
        dispatcher = DispatchWorkers(loop, tools, scenario_id=case.scenario.id)
        result = await loop.run(
            orchestrator_spec(), case.scenario.alert.render(), [*tools, dispatcher], scenario_id="s"
        )

    assert result.outcome is Outcome.ANSWERED
    returned = json.loads(orchestrator.seen[1][-1]["content"][0]["content"])
    assert [w["role"] for w in returned] == ["changes", "logs"]
    assert returned[0]["findings"]["evidence"] == ["cfg:d-1042:DB_POOL_MAX"]
    children = events.children(result.run_id)
    assert sorted(c.agent for c in children) == ["worker:changes", "worker:logs"]


async def test_workers_run_concurrently():
    barrier = asyncio.Barrier(2)

    @dataclass
    class Rendezvous:
        name: str
        read_only: bool = True
        idempotent: bool = True

        @property
        def definition(self) -> dict[str, Any]:
            return {"name": self.name, "description": "", "input_schema": {"type": "object"}}

        async def invoke(self, args: dict[str, Any]) -> ToolResult:
            # Deadlocks (and times out) unless both workers are inside a tool at once.
            await asyncio.wait_for(barrier.wait(), timeout=1)
            return ToolResult("met")

    tools = [Rendezvous("list_deploys"), Rendezvous("query_metrics")]
    worker = lambda name, id_: Scripted(  # noqa: E731
        [
            {"content": [call(name, {}, id_)]},
            {"content": [call("submit_findings", FINDINGS, id_ + "f")]},
        ]
    )
    loop = make_loop(
        Router(changes=worker("list_deploys", "c"), metrics=worker("query_metrics", "m"))
    )
    out = await DispatchWorkers(loop, tools, scenario_id="s").invoke(
        {"tasks": [{"role": "changes", "question": "q"}, {"role": "metrics", "question": "q"}]}
    )
    workers = json.loads(out.text)
    assert [w["outcome"] for w in workers] == ["answered", "answered"]


async def test_a_worker_cannot_call_a_gated_tool(case):
    changes = Scripted(
        [
            {"content": [call("rollback_deploy", {"deploy_id": "d-1042", "reason": "x"}, "r")]},
            {"content": [call("submit_findings", FINDINGS, "f")]},
        ]
    )
    loop = make_loop(Router(changes=changes), approve=True)
    async with mcp_tools(build_telemetry_server(case.store)) as tools:
        await DispatchWorkers(loop, tools, scenario_id="s").invoke(
            {"tasks": [{"role": "changes", "question": "q"}]}
        )
    denied = changes.seen[1][-1]["content"][0]
    assert denied["is_error"] and "unavailable" in denied["content"]
    assert case.store.rollbacks == {}


async def test_workers_and_orchestrator_share_one_budget(case):
    pricey = {"input_tokens": 0, "output_tokens": 60_000}  # $1.20 on Opus 5.5
    orchestrator = Scripted(
        [
            {"content": [dispatch("changes")]},
            {"content": [call("submit_diagnosis", DIAGNOSIS, "s")]},
        ]
    )
    changes = Scripted([{"content": [call("submit_findings", FINDINGS, "f")], "usage": pricey}])
    loop = make_loop(Router(orchestrator=orchestrator, changes=changes), budget=1.0)
    async with mcp_tools(build_telemetry_server(case.store)) as tools:
        result = await loop.run(
            orchestrator_spec(),
            "ALERT",
            [*tools, DispatchWorkers(loop, tools, scenario_id="s")],
            scenario_id="s",
        )
    assert result.outcome is Outcome.BUDGET_EXCEEDED
    assert len(orchestrator.seen) == 1
