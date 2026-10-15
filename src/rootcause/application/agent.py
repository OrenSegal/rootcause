"""The agent loop, written out by hand.

    model call -> stop-reason check -> run tools -> append results -> repeat

until the agent calls its terminal tool, or the step limit, the budget, or a
refusal ends the run. One loop serves every role (single investigator,
orchestrator, worker); an `AgentSpec` is what differs between them.

Every turn is logged before it is acted on, so `resume` can rebuild a run
that crashed and carry on with the same history.
"""

import asyncio
import time
from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from anthropic.types.beta import BetaMessage, BetaUsage
from pydantic import BaseModel

from rootcause.application.budget import Budget, BudgetExceededError
from rootcause.application.executor import (
    ExecSettings,
    Execution,
    Recovery,
    ToolCall,
    ToolExecutor,
)
from rootcause.application.ports import (
    ApprovalPolicy,
    EventLog,
    LLMProvider,
    Tool,
    ToolTraits,
    Tracer,
)
from rootcause.application.terminal import TerminalTool
from rootcause.application.tracing import (
    AGENT,
    OPERATION,
    REQUEST_MODEL,
    RESPONSE_MODEL,
    NullTracer,
    usage_attributes,
)
from rootcause.application.transcript import (
    ASSISTANT,
    OUTCOME,
    START,
    TOOL_CALL,
    TOOL_INTENT,
    USER,
    Transcript,
)
from rootcause.domain.answers import Outcome

# The run whose tools are executing. Tools that start sub-agents read it to link
# child runs to their parent; each asyncio task gets its own copy.
CURRENT_RUN: ContextVar[str | None] = ContextVar("rootcause_current_run", default=None)

NUDGE = "You stopped without calling {tool}. Call it now with your best answer and evidence ids."


@dataclass(frozen=True)
class AgentSpec[A: BaseModel]:
    name: str
    system_prompt: str
    terminal: TerminalTool[A]
    allowed_tools: frozenset[str] | None = None  # None: every tool the gateway offers
    allow_gated: bool = False
    max_steps: int = 15
    # Reject an answer citing evidence ids that no tool returned in this run.
    require_grounded_evidence: bool = True

    def select[T: ToolTraits](self, tools: Sequence[T]) -> list[T]:
        """The tools this agent may see, in a stable order (a changing order breaks caching)."""
        return [
            t
            for t in tools
            if (self.allowed_tools is None or t.name in self.allowed_tools)
            and (self.allow_gated or t.read_only)
        ]


class TokenUsage(BaseModel):
    """Token counts as the API reports them. `input_tokens` excludes cache reads
    and writes, as in the Messages API `usage` block."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, usage: BetaUsage) -> None:
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_read_tokens += usage.cache_read_input_tokens or 0
        self.cache_write_tokens += usage.cache_creation_input_tokens or 0


class RunResult[A: BaseModel](BaseModel):
    run_id: str
    agent: str
    outcome: Outcome
    answer: A | None = None
    steps: int
    tool_calls: list[ToolCall]
    cost_usd: float
    wall_ms: float
    note: str = ""
    usage: TokenUsage = TokenUsage()
    models: list[str] = []  # every model that served a turn, in first-seen order


@dataclass
class _Run:
    run_id: str
    spec: AgentSpec[Any]
    executor: ToolExecutor
    tools: list[Tool]
    transcript: Transcript
    calls: list[ToolCall]
    started: float
    usage: TokenUsage = field(default_factory=TokenUsage)
    models: list[str] = field(default_factory=list)

    def record(self, model: str, usage: BetaUsage) -> None:
        self.usage.add(usage)
        if model not in self.models:
            self.models.append(model)


class AgentLoop:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        events: EventLog,
        budget: Budget,
        approval: ApprovalPolicy,
        settings: ExecSettings | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self.provider = provider
        self.events = events
        self.budget = budget
        self.approval = approval
        self.settings = settings or ExecSettings()
        self.tracer: Tracer = tracer or NullTracer()

    async def run[A: BaseModel](
        self,
        spec: AgentSpec[A],
        task: str,
        tools: Sequence[Tool],
        *,
        scenario_id: str,
        parent_run_id: str | None = None,
    ) -> RunResult[A]:
        run_id = self.events.start_run(
            scenario_id=scenario_id,
            agent=spec.name,
            provider=self.provider.name,
            parent_run_id=parent_run_id,
        )
        selected = spec.select(tools)
        self.events.append(run_id, START, {"task": task, "tools": [t.name for t in selected]})
        run = self._prepare(run_id, spec, selected, Transcript.start(task))
        return await self._drive(run, Recovery())

    async def resume[A: BaseModel](
        self, run_id: str, spec: AgentSpec[A], tools: Sequence[Tool]
    ) -> RunResult[A]:
        """Continue a run from its event log after a crash."""
        events = self.events.events(run_id)
        if any(e.kind == OUTCOME for e in events):
            raise ValueError(f"run {run_id} already finished")
        transcript = Transcript.from_events(events)
        self.budget.spent_usd += transcript.spent_usd
        run = self._prepare(run_id, spec, spec.select(tools), transcript)
        recovery = Recovery()
        for e in events:
            if e.kind == ASSISTANT and "usage" in e.payload:
                run.record(e.payload["model"], BetaUsage.model_validate(e.payload["usage"]))
            elif e.kind == TOOL_CALL:
                run.calls.append(ToolCall.model_validate(e.payload["call"]))
                recovery.results[e.payload["call"]["tool_use_id"]] = e.payload["result"]
            elif e.kind == TOOL_INTENT:
                recovery.intents.add(e.payload["tool_use_id"])
        recovery.intents -= set(recovery.results)
        return await self._drive(run, recovery)

    def _prepare(
        self, run_id: str, spec: AgentSpec[Any], tools: list[Tool], transcript: Transcript
    ) -> _Run:
        executor = ToolExecutor(
            tools={t.name: t for t in tools},
            terminal=spec.terminal,
            approval=self.approval,
            events=self.events,
            settings=self.settings,
            seen=transcript.tool_result_texts(),
            grounded=spec.require_grounded_evidence,
            tracer=self.tracer,
        )
        return _Run(run_id, spec, executor, tools, transcript, [], time.perf_counter())

    async def _drive(self, run: _Run, recovery: Recovery) -> RunResult[Any]:
        token = CURRENT_RUN.set(run.run_id)
        attributes = {
            OPERATION: "invoke_agent",
            AGENT: run.spec.name,
            "rootcause.run_id": run.run_id,
        }
        try:
            with self.tracer.span(f"invoke_agent {run.spec.name}", **attributes) as span:
                result = await self._turns(run, recovery)
                span.set("rootcause.outcome", result.outcome.value)
                span.set("rootcause.steps", result.steps)
                span.set("rootcause.cost_usd", result.cost_usd)
                if result.outcome is Outcome.ERROR:
                    span.fail(result.note)
                return result
        finally:
            CURRENT_RUN.reset(token)

    async def _turns(self, run: _Run, recovery: Recovery) -> RunResult[Any]:
        spec, transcript = run.spec, run.transcript
        system = [{"type": "text", "text": spec.system_prompt}]
        definitions = [t.definition for t in run.tools] + [spec.terminal.definition]

        # A crash can land between a model turn and its tool results. Finish that turn first.
        if pending := transcript.pending_tool_uses():
            if (done := await self._run_tools(run, pending, recovery)) is not None:
                return done

        while transcript.steps < spec.max_steps:
            model = getattr(self.provider, "model", self.provider.name)
            try:
                self.budget.check()
                with self.tracer.span(
                    f"chat {model}", **{OPERATION: "chat", REQUEST_MODEL: model}
                ) as span:
                    response = await self.provider.complete(
                        system=system, messages=transcript.messages, tools=definitions
                    )
                    span.set(RESPONSE_MODEL, response.model)
                    span.set("gen_ai.response.finish_reasons", str(response.stop_reason))
                    for key, value in usage_attributes(response.usage).items():
                        span.set(key, value)
            except BudgetExceededError as e:
                return self._finish(run, Outcome.BUDGET_EXCEEDED, note=str(e))

            content = [b.model_dump(mode="json", exclude_none=True) for b in response.content]
            cost = self.budget.record(response.model, response.usage)
            run.record(response.model, response.usage)
            self.events.append(
                run.run_id,
                ASSISTANT,
                {
                    "content": content,
                    "model": response.model,
                    "stop_reason": response.stop_reason,
                    "cost_usd": cost,
                    "usage": response.usage.model_dump(mode="json"),
                },
            )
            transcript.add_assistant(content)
            transcript.spent_usd += cost

            # Stop reasons are checked before content is trusted.
            if (stop := self._stop(response)) is not None:
                return self._finish(run, *stop)

            uses = [b for b in content if b["type"] == "tool_use"]
            if not uses:
                if transcript.nudged:
                    return self._finish(run, Outcome.NO_ANSWER, note="ended without answering")
                nudge = NUDGE.format(tool=spec.terminal.name)
                self.events.append(run.run_id, USER, {"content": nudge, "nudge": True})
                transcript.add_user(nudge, nudge=True)
                continue

            if (done := await self._run_tools(run, uses, Recovery())) is not None:
                return done

        return self._finish(run, Outcome.STEP_LIMIT)

    async def _run_tools(
        self, run: _Run, uses: list[dict[str, Any]], recovery: Recovery
    ) -> RunResult[Any] | None:
        # Independent calls run concurrently; every result goes back in one user message.
        step = run.transcript.steps
        executions: list[Execution] = await asyncio.gather(
            *(run.executor.execute(run.run_id, step, use, recovery) for use in uses)
        )
        run.calls.extend(x.call for x in executions if x.call)
        if (answer := next((x.answer for x in executions if x.answer), None)) is not None:
            return self._finish(run, Outcome.ANSWERED, answer=answer)
        results = [x.result for x in executions]
        self.events.append(run.run_id, USER, {"content": results})
        run.transcript.add_user(results)
        return None

    @staticmethod
    def _stop(response: BetaMessage) -> tuple[Outcome, None, str] | None:
        if response.stop_reason == "refusal":
            return Outcome.REFUSED, None, str(response.stop_details)
        if response.stop_reason == "max_tokens":
            return Outcome.ERROR, None, "response hit max_tokens mid-turn"
        return None

    def _finish(
        self, run: _Run, outcome: Outcome, answer: BaseModel | None = None, note: str = ""
    ) -> RunResult[Any]:
        # Parametrized at runtime so the answer serializes and validates as its own type.
        result_type: type[RunResult[Any]] = RunResult[run.spec.terminal.answer]  # ty: ignore[invalid-type-form]
        result = result_type(
            run_id=run.run_id,
            agent=run.spec.name,
            outcome=outcome,
            answer=answer,
            steps=run.transcript.steps,
            tool_calls=run.calls,
            cost_usd=round(run.transcript.spent_usd, 6),
            wall_ms=round((time.perf_counter() - run.started) * 1000, 1),
            note=note,
            usage=run.usage,
            models=run.models,
        )
        self.events.append(run.run_id, OUTCOME, result.model_dump(mode="json"))
        return result
