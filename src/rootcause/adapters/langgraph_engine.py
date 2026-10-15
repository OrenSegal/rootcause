"""The investigation rebuilt on LangGraph, to compare a framework with the hand-written loop.

LangGraph owns the control flow here: state with reducers, a model node and a tools
node, conditional edges for the nudge, the step limit and the budget, and for the
multi-agent mode a map-reduce, where `Send` fans each dispatched question out to a
worker subgraph and a join node folds their findings back into the orchestrator's
conversation. What is shared with the hand-written loop, on purpose, is everything
that is not orchestration: the model provider, the briefs and tool subsets
(`AgentSpec`), the executor (approval gate, retries, grounding check), the budget
and the grader. So a difference between the two measures the orchestration layer.

Needs the langgraph extra (`uv sync --extra langgraph`); it is imported when a graph
is built, so importing this module does not require it.
"""

import asyncio
import json
import time
from operator import add
from typing import Annotated, Any, TypedDict

from rootcause.application.agent import NUDGE, AgentSpec, RunResult, TokenUsage
from rootcause.application.budget import Budget, BudgetExceededError
from rootcause.application.executor import ExecSettings, ToolCall, ToolExecutor
from rootcause.application.investigator import investigator_spec
from rootcause.application.orchestrator import (
    DISPATCH,
    DISPATCH_DEFINITION,
    orchestrator_spec,
    parse_tasks,
    worker_spec,
)
from rootcause.application.ports import ApprovalPolicy, Block, EventLog, LLMProvider, Message, Tool
from rootcause.application.transcript import OUTCOME
from rootcause.application.transcript import START as START_EVENT
from rootcause.domain.answers import Diagnosis, Outcome


class State(TypedDict, total=False):
    messages: Annotated[list[Message], add]
    reports: Annotated[list[dict[str, Any]], add]  # worker findings, tagged by dispatch call
    steps: int
    nudged: bool
    outcome: str
    note: str


def _uses(state: State) -> list[Block]:
    last = state["messages"][-1]
    if last["role"] != "assistant":
        return []
    return [b for b in last["content"] if b.get("type") == "tool_use"]


class LangGraphEngine:
    def __init__(self, provider: LLMProvider, settings: ExecSettings | None = None) -> None:
        self.provider = provider
        self.settings = settings or ExecSettings()
        _, _, rest = provider.name.partition(":")
        self.name = f"langgraph:{rest or provider.name}"

    async def investigate(
        self,
        task: str,
        tools: list[Tool],
        *,
        scenario_id: str,
        events: EventLog,
        budget: Budget,
        approval: ApprovalPolicy,
        multi: bool,
        retrieval: bool = False,
        brief: str | None = None,
    ) -> RunResult[Diagnosis]:
        spec = orchestrator_spec() if multi else investigator_spec(retrieval=retrieval)
        if brief is not None:
            spec = AgentSpec(
                name=spec.name,
                system_prompt=brief,
                terminal=spec.terminal,
                allowed_tools=spec.allowed_tools,
                allow_gated=spec.allow_gated,
                max_steps=spec.max_steps,
            )
        ctx = _Context(self, tools, scenario_id, events, budget, approval)
        return await ctx.run(spec, task, parent=None, multi=multi)


class _Context:
    """One investigation: what every graph in it shares."""

    def __init__(
        self,
        engine: LangGraphEngine,
        tools: list[Tool],
        scenario_id: str,
        events: EventLog,
        budget: Budget,
        approval: ApprovalPolicy,
    ) -> None:
        self.engine, self.tools, self.scenario_id = engine, tools, scenario_id
        self.events, self.budget, self.approval = events, budget, approval

    async def run(
        self, spec: AgentSpec[Any], task: str, *, parent: str | None, multi: bool = False
    ) -> RunResult[Any]:
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import Send

        events, provider = self.events, self.engine.provider
        run_id = events.start_run(
            scenario_id=self.scenario_id,
            agent=spec.name,
            provider=self.engine.name,
            parent_run_id=parent,
        )
        selected = [t for t in spec.select(self.tools) if t.name != DISPATCH]
        events.append(run_id, START_EVENT, {"task": task, "tools": [t.name for t in selected]})
        executor = ToolExecutor(
            tools={t.name: t for t in selected},
            terminal=spec.terminal,
            approval=self.approval,
            events=events,
            settings=self.engine.settings,
            grounded=spec.require_grounded_evidence,
        )
        definitions = [t.definition for t in selected]
        if multi:
            definitions.append(DISPATCH_DEFINITION)
        definitions.append(spec.terminal.definition)
        system = [{"type": "text", "text": spec.system_prompt}]
        usage, models, calls = TokenUsage(), list[str](), list[ToolCall]()
        spent = [0.0]
        answer: list[Any] = [None]
        started = time.perf_counter()

        async def model(state: State) -> State:
            try:
                self.budget.check()
                response = await provider.complete(
                    system=system, messages=state["messages"], tools=definitions
                )
            except BudgetExceededError as e:
                return {"outcome": Outcome.BUDGET_EXCEEDED, "note": str(e)}
            except Exception as e:
                return {"outcome": Outcome.ERROR, "note": f"model call failed: {e}"}
            spent[0] += self.budget.record(response.model, response.usage)
            usage.add(response.usage)
            if response.model not in models:
                models.append(response.model)
            content = [b.model_dump(mode="json", exclude_none=True) for b in response.content]
            update: State = {
                "messages": [{"role": "assistant", "content": content}],
                "steps": state.get("steps", 0) + 1,
            }
            if response.stop_reason == "refusal":
                update |= {"outcome": Outcome.REFUSED, "note": str(response.stop_details)}
            elif response.stop_reason == "max_tokens":
                update |= {"outcome": Outcome.ERROR, "note": "response hit max_tokens mid-turn"}
            return update

        def after_model(state: State) -> str | list[Send]:
            if state.get("outcome"):
                return END
            uses = _uses(state)
            if not uses:
                return "give_up" if state.get("nudged") else "nudge"
            dispatches = [u for u in uses if u["name"] == DISPATCH]
            if not dispatches:
                return "tools"
            sends = [
                Send("worker", {"role": role, "question": q, "call_id": u["id"]})
                for u in dispatches
                if not isinstance(tasks := parse_tasks(u.get("input") or {}), str)
                for role, q in tasks
            ]
            return sends or "join"

        async def execute(state: State, uses: list[Block]) -> tuple[list[Block], Any]:
            step = state.get("steps", 0)
            done = await asyncio.gather(*(executor.execute(run_id, step, u) for u in uses))
            calls.extend(x.call for x in done if x.call)
            return [x.result for x in done], next((x.answer for x in done if x.answer), None)

        async def tools_node(state: State) -> State:
            results, found = await execute(state, _uses(state))
            if found is not None:
                answer[0] = found
                return {"outcome": Outcome.ANSWERED}
            return {"messages": [{"role": "user", "content": results}]}

        async def worker(state: dict[str, Any]) -> State:  # state is one Send payload
            r = await self.run(worker_spec(state["role"]), state["question"], parent=run_id)
            report = {
                "role": state["role"],
                "run_id": r.run_id,
                "outcome": r.outcome.value,
                "findings": r.answer.model_dump() if r.answer else None,
                "note": r.note,
            }
            return {"reports": [{"call_id": state["call_id"], **report}]}

        async def join(state: State) -> State:
            uses = _uses(state)
            others = [u for u in uses if u["name"] != DISPATCH]
            results, found = await execute(state, others) if others else ([], None)
            by_id = {r["tool_use_id"]: r for r in results}
            ordered = []
            for u in uses:
                if u["name"] != DISPATCH:
                    ordered.append(by_id[u["id"]])
                    continue
                parsed = parse_tasks(u.get("input") or {})
                reports = [
                    {k: v for k, v in r.items() if k != "call_id"}
                    for r in state.get("reports", [])
                    if r["call_id"] == u["id"]
                ]
                text, error = (
                    (parsed, True) if isinstance(parsed, str) else (json.dumps(reports), False)
                )
                ordered.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": u["id"],
                        "content": text,
                        "is_error": error,
                    }
                )
                if not error:
                    executor.seen.append(text)  # findings are evidence the orchestrator may cite
            if found is not None:
                answer[0] = found
                return {"outcome": Outcome.ANSWERED}
            return {"messages": [{"role": "user", "content": ordered}]}

        def after_tools(state: State) -> str:
            if state.get("outcome"):
                return END
            return "limit" if state.get("steps", 0) >= spec.max_steps else "model"

        def nudge(state: State) -> State:
            text = NUDGE.format(tool=spec.terminal.name)
            return {"messages": [{"role": "user", "content": text}], "nudged": True}

        def give_up(state: State) -> State:
            return {"outcome": Outcome.NO_ANSWER, "note": "ended without answering"}

        def limit(state: State) -> State:
            return {"outcome": Outcome.STEP_LIMIT}

        # ty doesn't yet match a TypedDict against LangGraph's TypedDictLike bound.
        graph = StateGraph(State)  # ty: ignore[invalid-argument-type]
        graph.add_node("model", model)
        graph.add_node("tools", tools_node)
        graph.add_node("worker", worker)
        graph.add_node("join", join)
        graph.add_node("nudge", nudge)
        graph.add_node("give_up", give_up)
        graph.add_node("limit", limit)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", after_model)
        graph.add_conditional_edges("tools", after_tools)
        graph.add_edge("worker", "join")
        graph.add_conditional_edges("join", after_tools)
        graph.add_edge("nudge", "model")
        graph.add_edge("give_up", END)
        graph.add_edge("limit", END)
        app = graph.compile()

        final = await app.ainvoke(
            {"messages": [{"role": "user", "content": task}], "steps": 0},
            config={"recursion_limit": 6 * spec.max_steps + 10},
        )
        result_type: type[RunResult[Any]] = RunResult[spec.terminal.answer]  # ty: ignore[invalid-type-form]
        result = result_type(
            run_id=run_id,
            agent=spec.name,
            outcome=Outcome(final.get("outcome", Outcome.ERROR)),
            answer=answer[0],
            steps=final.get("steps", 0),
            tool_calls=calls,
            cost_usd=round(spent[0], 6),
            wall_ms=round((time.perf_counter() - started) * 1000, 1),
            note=final.get("note", ""),
            usage=usage,
            models=models,
        )
        events.append(run_id, OUTCOME, result.model_dump(mode="json"))
        return result
