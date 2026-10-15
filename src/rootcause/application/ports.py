"""The interfaces the application depends on. Adapters implement them.

The loop speaks the Messages API's content-block format, so responses are the
SDK's `BetaMessage` data type (see docs/AUDIT.md, "Deliberate exceptions").
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from anthropic.types.beta import BetaMessage

Block = dict[str, Any]
Message = dict[str, Any]


class LLMProvider(Protocol):
    name: str

    async def complete(
        self, *, system: list[Block], messages: list[Message], tools: list[Block]
    ) -> BetaMessage: ...


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False


class ToolTraits(Protocol):
    """The part of a tool that access decisions need (which agent may see it)."""

    @property
    def name(self) -> str: ...
    @property
    def read_only(self) -> bool: ...


class Tool(ToolTraits, Protocol):
    """Anything the model can call. `read_only` and `idempotent` drive gating and retries."""

    @property
    def definition(self) -> Block: ...
    @property
    def idempotent(self) -> bool: ...

    async def invoke(self, args: dict[str, Any]) -> ToolResult: ...


@dataclass(frozen=True)
class Decision:
    approved: bool
    by: str
    note: str = ""


class ApprovalPolicy(Protocol):
    async def decide(self, tool: str, args: dict[str, Any]) -> Decision: ...


@dataclass(frozen=True)
class Event:
    seq: int
    kind: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    scenario_id: str
    agent: str
    provider: str
    parent_run_id: str | None


class EventLog(Protocol):
    """Append-only. Rows are never updated; resume and grading read them back."""

    def start_run(
        self, *, scenario_id: str, agent: str, provider: str, parent_run_id: str | None = None
    ) -> str: ...

    def append(self, run_id: str, kind: str, payload: Mapping[str, Any]) -> None: ...

    def events(self, run_id: str) -> list[Event]: ...

    def run_info(self, run_id: str) -> RunInfo: ...

    def children(self, run_id: str) -> list[RunInfo]: ...
