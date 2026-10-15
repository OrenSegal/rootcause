from pathlib import Path
from typing import Any

import pytest
from anthropic.types.beta import BetaMessage

from rootcause.adapters.fixtures import load_case
from rootcause.adapters.sqlite_events import SqliteEventLog
from rootcause.application.agent import AgentLoop
from rootcause.application.approval import StaticApproval
from rootcause.application.budget import Budget
from rootcause.composition import PRICES
from rootcause.domain.scenario import Case
from rootcause.domain.store import TelemetryStore

SCENARIOS = Path(__file__).resolve().parent.parent / "scenarios"


@pytest.fixture
def scenario_dir() -> Path:
    return SCENARIOS / "001-checkout-pool-config"


@pytest.fixture
def case(scenario_dir: Path) -> Case:
    return load_case(scenario_dir)


@pytest.fixture
def store(case: Case) -> TelemetryStore:
    return case.store


class Scripted:
    """Replays a fixed list of assistant turns and records what it was sent.
    A turn that is an Exception is raised instead, to simulate a crash."""

    name = "scripted"

    def __init__(self, turns: list[Any], model: str = "claude-opus-5-5") -> None:
        self.turns = turns
        self.model = model
        self.seen: list[list[dict[str, Any]]] = []

    async def complete(self, *, system, messages, tools) -> BetaMessage:
        self.seen.append([dict(m) for m in messages])
        self.tools = tools
        turn = self.turns[len(self.seen) - 1]
        if isinstance(turn, BaseException):
            raise turn
        return BetaMessage.model_validate(
            {
                "id": f"msg_{len(self.seen)}",
                "type": "message",
                "role": "assistant",
                "model": self.model,
                "stop_reason": turn.get("stop_reason", "tool_use"),
                "stop_sequence": None,
                "content": turn["content"],
                "usage": turn.get("usage", {"input_tokens": 1000, "output_tokens": 200}),
            }
        )


def call(name: str, args: dict[str, Any], id_: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": id_, "name": name, "input": args}


def make_loop(provider, *, approve=False, budget=1.0, events=None, settings=None) -> AgentLoop:
    return AgentLoop(
        provider=provider,
        events=events or SqliteEventLog(),
        budget=Budget(limit_usd=budget, prices=PRICES),
        approval=StaticApproval(approve),
        settings=settings,
    )
