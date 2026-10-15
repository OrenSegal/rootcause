"""The conversation, as an append-only list built from the event log.

Messages are only ever appended. Each assistant turn goes back exactly as it
was logged, thinking blocks included, which is what preserved thinking needs
and what makes a resumed run send the same history as the original.
"""

from dataclasses import dataclass, field
from typing import Any

from rootcause.application.ports import Block, Event, Message

START, ASSISTANT, USER = "start", "assistant", "user"
TOOL_INTENT, TOOL_CALL, APPROVAL, OUTCOME = "tool_intent", "tool_call", "approval", "outcome"


@dataclass
class Transcript:
    messages: list[Message] = field(default_factory=list)
    steps: int = 0
    nudged: bool = False
    spent_usd: float = 0.0

    @classmethod
    def start(cls, task: str) -> Transcript:
        return cls(messages=[{"role": "user", "content": task}])

    def add_assistant(self, content: list[Block]) -> None:
        self.messages.append({"role": "assistant", "content": content})
        self.steps += 1

    def add_user(self, content: str | list[Block], *, nudge: bool = False) -> None:
        self.messages.append({"role": "user", "content": content})
        self.nudged = self.nudged or nudge

    def pending_tool_uses(self) -> list[Block]:
        """Tool calls in the last assistant turn that have no results yet (after a crash)."""
        if not self.messages or self.messages[-1]["role"] != "assistant":
            return []
        return [b for b in self.messages[-1]["content"] if b.get("type") == "tool_use"]

    def tool_result_texts(self) -> list[str]:
        """Successful tool results so far, for the grounding check on a resumed run."""
        return [
            b["content"]
            for m in self.messages
            if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"]
            if b.get("type") == "tool_result"
            and not b.get("is_error")
            and isinstance(b.get("content"), str)
        ]

    @classmethod
    def from_events(cls, events: list[Event]) -> Transcript:
        transcript: Transcript | None = None
        for event in events:
            p: dict[str, Any] = event.payload
            if event.kind == START:
                transcript = cls.start(p["task"])
            elif transcript is None:
                raise ValueError("event log does not begin with a start event")
            elif event.kind == ASSISTANT:
                transcript.add_assistant(p["content"])
                transcript.spent_usd += p["cost_usd"]
            elif event.kind == USER:
                transcript.add_user(p["content"], nudge=p.get("nudge", False))
        if transcript is None:
            raise ValueError("empty event log")
        return transcript
