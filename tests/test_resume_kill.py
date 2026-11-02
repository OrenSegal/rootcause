"""Crash recovery against a real kill: a child process is sent SIGKILL inside a side
effect, with nothing given a chance to clean up, and a new loop resumes from its log file."""

import asyncio
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from conftest import Scripted, call, make_loop

from rootcause.adapters.sqlite_events import SqliteEventLog
from rootcause.application.executor import ExecSettings
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


@dataclass
class Logs:
    name: str = "query_logs"
    read_only: bool = True
    idempotent: bool = True
    definition: ClassVar[dict[str, Any]] = {
        "name": "query_logs",
        "description": "",
        "input_schema": {"type": "object"},
    }

    async def invoke(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult("saw d-1042")


@dataclass
class Page:
    """A side effect recorded on disk, then a hang so the parent can kill us inside the call."""

    effects: Path
    name: str = "page_oncall"
    read_only: bool = False
    idempotent: bool = False
    definition: ClassVar[dict[str, Any]] = {
        "name": "page_oncall",
        "description": "",
        "input_schema": {"type": "object"},
    }

    async def invoke(self, args: dict[str, Any]) -> ToolResult:
        with self.effects.open("a") as f:
            f.write("paged\n")
        await asyncio.sleep(600)
        return ToolResult("paged")


async def _child(log: str, effects: str) -> None:
    provider = Scripted([{"content": [call("query_logs", {}, "a"), call("page_oncall", {}, "b")]}])
    loop = make_loop(
        provider, approve=True, events=SqliteEventLog(log), settings=ExecSettings(timeout_s=600)
    )
    tools = [Logs(), Page(Path(effects))]
    await loop.run(investigator_spec(), "ALERT", tools, scenario_id="s")


async def test_a_killed_process_resumes_from_its_log_without_repeating_the_side_effect(tmp_path):
    log, effects = tmp_path / "runs.sqlite", tmp_path / "effects"
    child = await asyncio.create_subprocess_exec(sys.executable, __file__, str(log), str(effects))
    try:
        deadline = time.monotonic() + 120
        while not effects.exists():  # slow imports on a loaded machine, not a slow test
            assert child.returncode is None, "child exited before reaching the side effect"
            assert time.monotonic() < deadline, "child never reached the side effect"
            await asyncio.sleep(0.02)
    finally:
        if child.returncode is None:
            child.send_signal(signal.SIGKILL)
        await child.wait()
    assert child.returncode == -signal.SIGKILL

    events = SqliteEventLog(log)
    ((run_id,),) = events.db.execute("select run_id from runs").fetchall()
    provider = Scripted([{"content": [call("submit_diagnosis", DIAGNOSIS, "c")]}])
    result = await make_loop(provider, approve=True, events=events).resume(
        run_id, investigator_spec(), [Logs(), Page(effects)]
    )

    assert result.outcome is Outcome.ANSWERED  # citing an id only the recovered result held
    assert effects.read_text() == "paged\n"  # at most once
    reused, unknown = provider.seen[0][-1]["content"]
    assert reused["content"] == "saw d-1042"
    assert unknown["is_error"] and "outcome is unknown" in unknown["content"]


if __name__ == "__main__":
    asyncio.run(_child(sys.argv[1], sys.argv[2]))
