"""Orchestrator-worker investigation.

The orchestrator does not read telemetry itself. It sends focused questions to
workers, each of which runs the same agent loop with a read-only subset of the
tools, then it decides. Why split it this way:
- Each worker's context holds one kind of evidence, not all of it.
- Workers run concurrently, so wall-clock time is the slowest worker, not the sum.
- Only the orchestrator can reach a gated tool; workers can't change anything.
Whether this beats one agent with every tool is an empirical question; the
evals answer it (`rootcause compare`).
"""

import asyncio
import json
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from rootcause.application.agent import CURRENT_RUN, AgentLoop, AgentSpec
from rootcause.application.investigator import SUBMIT_DIAGNOSIS
from rootcause.application.ports import Block, Tool, ToolResult
from rootcause.application.terminal import TerminalTool
from rootcause.domain.answers import Diagnosis, Findings

SUBMIT_FINDINGS = TerminalTool("submit_findings", Findings)
MAX_WORKERS = 3


class WorkerRole(StrEnum):
    CHANGES = "changes"
    LOGS = "logs"
    METRICS = "metrics"


WORKER_TOOLS: dict[WorkerRole, frozenset[str]] = {
    WorkerRole.CHANGES: frozenset({"list_deploys", "diff_config"}),
    WorkerRole.LOGS: frozenset({"query_logs", "search_traces", "get_trace"}),
    WorkerRole.METRICS: frozenset({"query_metrics"}),
}

WORKER_FOCUS: dict[WorkerRole, str] = {
    WorkerRole.CHANGES: "what changed (deploys and configuration) and when",
    WorkerRole.LOGS: "what the logs and traces show, and when errors began in each service",
    WorkerRole.METRICS: "what the metrics show, and which service moved first",
}


def worker_spec(role: WorkerRole, max_steps: int = 8) -> AgentSpec[Findings]:
    prompt = f"""\
[{role}] You are one of several investigators working an incident. Your focus is \
{WORKER_FOCUS[role]}. Answer the question you were given using your tools, then call \
{SUBMIT_FINDINGS.name} once. Report what the evidence shows, including evidence that \
points away from the obvious suspect. Cite only ids the tools returned."""
    return AgentSpec(
        name=f"worker:{role}",
        system_prompt=prompt,
        terminal=SUBMIT_FINDINGS,
        allowed_tools=WORKER_TOOLS[role],
        allow_gated=False,
        max_steps=max_steps,
    )


ORCHESTRATOR_PROMPT = f"""\
[orchestrator] You lead an incident investigation. You cannot read telemetry \
yourself: call dispatch_workers with up to {MAX_WORKERS} focused questions, one per \
role ({", ".join(WorkerRole)}). Workers run in parallel and report findings with \
evidence ids. Dispatch again if the findings conflict or leave a gap.

The paging service is not always the cause. Weigh what changed, and when, against \
when each service started failing. rollback_deploy changes production and needs \
operator approval; request it only when the evidence supports it.

Finish by calling {SUBMIT_DIAGNOSIS.name}, citing evidence ids from the findings."""


def orchestrator_spec(max_steps: int = 8) -> AgentSpec[Diagnosis]:
    return AgentSpec(
        name="orchestrator",
        system_prompt=ORCHESTRATOR_PROMPT,
        terminal=SUBMIT_DIAGNOSIS,
        allowed_tools=frozenset({"dispatch_workers", "rollback_deploy"}),
        allow_gated=True,
        max_steps=max_steps,
    )


class DispatchWorkers:
    """A local tool: runs worker agents concurrently on the same loop, so they share
    its provider, event log and budget. Read-only, because workers are."""

    name = "dispatch_workers"
    read_only = True
    idempotent = True

    def __init__(self, loop: AgentLoop, tools: Sequence[Tool], *, scenario_id: str) -> None:
        self.loop = loop
        self.tools = list(tools)
        self.scenario_id = scenario_id

    @property
    def definition(self) -> Block:
        return {
            "name": self.name,
            "description": (
                "Send focused questions to worker investigators, who run in parallel and "
                "return findings with evidence ids. One task per role."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tasks"],
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["role", "question"],
                            "properties": {
                                "role": {"type": "string", "enum": [r.value for r in WorkerRole]},
                                "question": {"type": "string"},
                            },
                        },
                    }
                },
            },
        }

    async def invoke(self, args: dict[str, Any]) -> ToolResult:
        try:
            tasks = [(WorkerRole(t["role"]), str(t["question"])) for t in args["tasks"]]
        except (KeyError, TypeError, ValueError) as e:
            return ToolResult(f"Invalid tasks: {e}", is_error=True)
        if not tasks or len(tasks) > MAX_WORKERS:
            return ToolResult(f"Send between 1 and {MAX_WORKERS} tasks.", is_error=True)

        parent = CURRENT_RUN.get()
        results = await asyncio.gather(
            *(
                self.loop.run(
                    worker_spec(role),
                    question,
                    self.tools,
                    scenario_id=self.scenario_id,
                    parent_run_id=parent,
                )
                for role, question in tasks
            )
        )
        report = [
            {
                "role": role.value,
                "run_id": r.run_id,
                "outcome": r.outcome.value,
                "findings": r.answer.model_dump() if r.answer else None,
                "note": r.note,
            }
            for (role, _), r in zip(tasks, results, strict=True)
        ]
        return ToolResult(json.dumps(report))
