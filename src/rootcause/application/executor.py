"""Runs one tool call: approval, intent logging, timeout, retries, result logging.

Delivery semantics, stated plainly:
- Read-only and idempotent tools are at-least-once: retried on failure, and
  re-run on resume if a crash left no result.
- Anything else is at-most-once: never retried, and an intent written before
  the call means a crash between call and result is reported to the model as
  "outcome unknown" instead of being run a second time.
"""

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from rootcause.application.ports import ApprovalPolicy, Block, EventLog, Tool, ToolResult
from rootcause.application.terminal import TerminalTool
from rootcause.application.transcript import APPROVAL, TOOL_CALL, TOOL_INTENT


class ToolCall(BaseModel):
    step: int
    tool_use_id: str
    name: str
    args: dict[str, Any]
    gated: bool
    approved: bool | None = None
    is_error: bool = False
    attempts: int = 0
    ms: float = 0
    recovered: bool = False


@dataclass(frozen=True)
class ExecSettings:
    timeout_s: float = 10.0
    max_attempts: int = 3
    backoff_s: float = 0.2


@dataclass
class Recovery:
    """What the log says happened to tool calls in the turn a crash interrupted."""

    results: dict[str, Block] = field(default_factory=dict)  # tool_use_id -> logged tool_result
    intents: set[str] = field(default_factory=set)  # tool_use_ids started but not finished


@dataclass(frozen=True)
class Execution:
    result: Block
    call: ToolCall | None = None
    answer: BaseModel | None = None


UNGROUNDED = (
    "Evidence ids not returned by any tool in this run: {ids}. "
    "Cite only ids that appear in tool results, then resubmit."
)


def tool_result(tool_use_id: str, text: str, is_error: bool = False) -> Block:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": text,
        "is_error": is_error,
    }


class ToolExecutor:
    def __init__(
        self,
        *,
        tools: Mapping[str, Tool],
        terminal: TerminalTool,
        approval: ApprovalPolicy,
        events: EventLog,
        settings: ExecSettings,
        seen: list[str] | None = None,
        grounded: bool = True,
    ) -> None:
        self.tools = tools
        self.terminal = terminal
        self.approval = approval
        self.events = events
        self.settings = settings
        # Text of every successful tool result in this run: what the agent may cite.
        self.seen = seen if seen is not None else []
        self.grounded = grounded

    async def execute(
        self, run_id: str, step: int, use: Block, recovery: Recovery | None = None
    ) -> Execution:
        use_id, name = use["id"], use["name"]
        raw = use.get("input")
        args: dict[str, Any] = raw if isinstance(raw, dict) else {}

        if name == self.terminal.name:
            answer = self.terminal.parse(args)
            if isinstance(answer, str):
                return Execution(tool_result(use_id, answer, True))
            if self.grounded and (unseen := self.unseen_evidence(answer)):
                return Execution(
                    tool_result(use_id, UNGROUNDED.format(ids=", ".join(unseen)), True)
                )
            return Execution(tool_result(use_id, "Recorded."), answer=answer)

        tool = self.tools.get(name)
        if tool is None:
            return Execution(tool_result(use_id, f"Unknown or unavailable tool {name!r}.", True))

        call = ToolCall(
            step=step, tool_use_id=use_id, name=name, args=args, gated=not tool.read_only
        )

        if recovery and use_id in recovery.results:
            # Already ran and logged before the crash: reuse the result, don't run it again.
            return Execution(recovery.results[use_id])
        if recovery and use_id in recovery.intents and not tool.idempotent:
            call.recovered, call.is_error = True, True
            text = (
                f"{name} was started before a crash and its outcome is unknown. "
                "Check the current state before trying again."
            )
            return self._finish(run_id, call, tool_result(use_id, text, True))

        if call.gated:
            decision = await self.approval.decide(name, args)
            call.approved = decision.approved
            self.events.append(
                run_id,
                APPROVAL,
                {"step": step, "tool_use_id": use_id, "tool": name, "args": args, **vars(decision)},
            )
            if not decision.approved:
                call.is_error = True
                text = f"Operator denied {name}. {decision.note}".strip()
                return self._finish(run_id, call, tool_result(use_id, text, True))

        if not tool.read_only:
            self.events.append(run_id, TOOL_INTENT, {"tool_use_id": use_id, "name": name})

        outcome = await self._invoke(tool, args, call)
        return self._finish(run_id, call, tool_result(use_id, outcome.text, outcome.is_error))

    async def _invoke(self, tool: Tool, args: dict[str, Any], call: ToolCall) -> ToolResult:
        # Only idempotent tools are retried; a retried side effect could apply twice.
        attempts = self.settings.max_attempts if tool.idempotent else 1
        started = time.perf_counter()
        try:
            for attempt in range(1, attempts + 1):
                call.attempts = attempt
                try:
                    async with asyncio.timeout(self.settings.timeout_s):
                        return await tool.invoke(args)
                except Exception as e:  # timeouts, transport and protocol errors
                    if attempt == attempts:
                        return ToolResult(
                            f"{tool.name} failed after {attempt} attempt(s): "
                            f"{type(e).__name__}: {e}",
                            is_error=True,
                        )
                    await asyncio.sleep(self.settings.backoff_s * 2 ** (attempt - 1))
            raise AssertionError("unreachable")
        finally:
            call.ms = round((time.perf_counter() - started) * 1000, 1)

    def unseen_evidence(self, answer: BaseModel) -> list[str]:
        """Cited ids that no tool returned. Tools return JSON, so an id the agent saw
        appears as a quoted string in some result."""
        cited: list[str] = getattr(answer, "evidence", None) or []
        return [e for e in cited if not any(f'"{e}"' in text for text in self.seen)]

    def _finish(self, run_id: str, call: ToolCall, result: Block) -> Execution:
        call.is_error = call.is_error or bool(result["is_error"])
        if not result["is_error"] and isinstance(result["content"], str):
            self.seen.append(result["content"])
        self.events.append(run_id, TOOL_CALL, {"call": call.model_dump(), "result": result})
        return Execution(result, call)
