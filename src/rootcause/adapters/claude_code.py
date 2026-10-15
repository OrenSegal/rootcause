"""Claude Code as the harness: headless `claude -p` driving the telemetry MCP server.

This is the comparison row for the hand-written loop. Claude Code owns the loop,
so it is not an `LLMProvider`; it produces the same `RunResult[Diagnosis]` and is
graded by the same code. It runs on whatever Claude Code is signed in with,
which can be a subscription rather than an API key.

Isolation, so the only thing the model can use is the scenario's telemetry:
built-in tools off (`--tools ""`), no user or project settings or hooks
(`--setting-sources ""`), only this MCP server (`--strict-mcp-config`), an empty
working directory, and fixtures staged without the scenario's ground truth.

How the stream maps onto `RunResult`:
- steps: distinct assistant message ids (one per model turn).
- tool_calls: every `tool_use` except `StructuredOutput`, which is how the answer
  is handed in. A gated call counts as approved only if the operator approves
  and Claude Code reported no denial for it.
- outcome: from the `result` event's subtype and `is_error`, and whether
  `structured_output` parses as a `Diagnosis`. A failure of the harness itself
  (bad sign-in, crash, timeout, unparseable output) is an error, not a wrong answer.
- cost_usd: the `total_cost_usd` Claude Code reports, which is what the run
  would cost at API rates. On a subscription nothing is billed per call. A run
  that ends without a `result` event reports no cost, so its cost is unknown.
"""

import asyncio
import json
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from rootcause.adapters.fixtures import write_case
from rootcause.adapters.mcp_telemetry import build_telemetry_server
from rootcause.adapters.mcp_tools import mcp_tools
from rootcause.application.agent import RunResult, TokenUsage
from rootcause.application.executor import ToolCall
from rootcause.application.investigator import investigator_prompt
from rootcause.application.ports import EventLog, ToolTraits
from rootcause.application.transcript import OUTCOME, TOOL_CALL
from rootcause.domain.answers import Diagnosis, Outcome
from rootcause.domain.scenario import Case

SERVER = "telemetry"
PREFIX = f"mcp__{SERVER}__"
ANSWER_TOOL = "StructuredOutput"
# Written into the stream by `spawn_claude` when the process itself fails.
SPAWN_ERROR = "rootcause_spawn_error"
AGENT = "claude-code"
TIMEOUT_S = 900

PROMPT = investigator_prompt(
    report="report it as the structured diagnosis",
    finish="Finish with the structured diagnosis. Do not answer in plain text.",
)

OUTCOMES = {
    "error_max_turns": Outcome.STEP_LIMIT,
    "error_max_budget_usd": Outcome.BUDGET_EXCEEDED,
}

Spawn = Callable[[list[str], Path], Awaitable[list[str]]]


def allowed_tools(tools: Sequence[ToolTraits], *, approve: bool) -> list[str]:
    """Read-only tools always run. Gated tools stay visible either way, but only an
    approving operator lets them run, so a denial reaches the model as an error."""
    return [t.name for t in tools if t.read_only or approve]


def command(
    *,
    mcp_config: Path,
    allowed: Sequence[str],
    model: str,
    effort: str,
    max_turns: int,
    budget_usd: float,
) -> list[str]:
    return [
        "claude", "-p",
        "--system-prompt", PROMPT,
        "--model", model,
        "--effort", effort,
        "--tools", "",
        "--setting-sources", "",
        "--strict-mcp-config",
        "--mcp-config", str(mcp_config),
        "--allowedTools", ",".join(PREFIX + name for name in allowed),
        "--json-schema", json.dumps(Diagnosis.model_json_schema()),
        "--max-turns", str(max_turns),
        "--max-budget-usd", str(budget_usd),
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
    ]  # fmt: skip


def stage_case(case: Case, out: Path) -> Path:
    """Fixtures for the MCP server subprocess, with the answer removed."""
    hidden = Case(scenario=case.scenario.model_copy(update={"truth": None}), store=case.store)
    return write_case(hidden, out)


def server_command(fixtures: Path) -> list[str]:
    """The `rootcause` entry point installed next to this interpreter."""
    exe = shutil.which("rootcause", path=str(Path(sys.executable).parent)) or "rootcause"
    return [exe, "telemetry-mcp", str(fixtures)]


def spawn_error(message: str) -> str:
    return json.dumps({"type": SPAWN_ERROR, "message": message})


def parse_stream(
    lines: Iterable[str], *, run_id: str, gated: frozenset[str], approve: bool
) -> RunResult[Diagnosis]:
    turns: list[str] = []
    models: list[str] = []
    uses: list[tuple[int, dict[str, Any]]] = []
    errors: dict[str, bool] = {}
    denied: set[str] = set()
    final: dict[str, Any] | None = None
    problems: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"unparseable output line: {line[:200]!r}")
            continue
        kind = e.get("type")
        if kind == SPAWN_ERROR:
            problems.append(e["message"])
        elif kind == "assistant":
            msg = e["message"]
            if msg["id"] not in turns:
                turns.append(msg["id"])
            if (model := msg.get("model")) and model not in models:
                models.append(model)
            step = turns.index(msg["id"]) + 1
            uses += [(step, b) for b in msg["content"] if b["type"] == "tool_use"]
        elif kind == "user":
            for b in e["message"]["content"]:
                if b.get("type") == "tool_result":
                    errors[b["tool_use_id"]] = bool(b.get("is_error"))
        elif kind == "system" and e.get("subtype") == "permission_denied":
            denied.add(e["tool_use_id"])
        elif kind == "result":
            final = e
            denied |= {d["tool_use_id"] for d in e.get("permission_denials") or []}

    calls = []
    for step, b in uses:
        if b["name"] == ANSWER_TOOL:
            continue
        name = b["name"].removeprefix(PREFIX)
        is_gated = name in gated
        calls.append(
            ToolCall(
                step=step,
                tool_use_id=b["id"],
                name=name,
                args=b["input"],
                gated=is_gated,
                approved=(approve and b["id"] not in denied) if is_gated else None,
                is_error=errors.get(b["id"], False),
            )
        )

    answer, outcome, note = None, Outcome.ERROR, "no result event"
    if final is not None:
        subtype = final.get("subtype", "")
        note = subtype
        if final.get("is_error") and subtype == "success":
            note = f"harness error: {str(final.get('result', ''))[:500]}"
        elif subtype == "success":
            try:
                answer = Diagnosis.model_validate(final.get("structured_output"))
                outcome = Outcome.ANSWERED
            except ValidationError as err:
                outcome, note = Outcome.NO_ANSWER, f"invalid structured output: {err}"
        else:
            outcome = OUTCOMES.get(subtype, Outcome.ERROR)
    if problems:
        if outcome is Outcome.ANSWERED:
            note = "; ".join(problems)
        else:
            outcome, note = Outcome.ERROR, "; ".join([*problems, note])
    return RunResult[Diagnosis](
        run_id=run_id,
        agent=AGENT,
        outcome=outcome,
        answer=answer,
        steps=len(turns),
        tool_calls=calls,
        cost_usd=float((final or {}).get("total_cost_usd") or 0.0),
        wall_ms=float((final or {}).get("duration_ms") or 0.0),
        note=note,
        usage=_usage((final or {}).get("usage") or {}),
        models=models,
    )


def _usage(raw: dict[str, Any]) -> TokenUsage:
    return TokenUsage(
        input_tokens=raw.get("input_tokens") or 0,
        output_tokens=raw.get("output_tokens") or 0,
        cache_read_tokens=raw.get("cache_read_input_tokens") or 0,
        cache_write_tokens=raw.get("cache_creation_input_tokens") or 0,
    )


async def spawn_claude(cmd: list[str], cwd: Path) -> list[str]:
    """Stdout lines as they arrived. A timeout keeps what came before it, and a
    failure of the process is appended as a `SPAWN_ERROR` line with stderr."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:  # most often: the claude CLI is not installed
        return [spawn_error(f"could not start {cmd[0]}: {e}")]
    assert proc.stdout is not None and proc.stderr is not None
    stdout, stderr = proc.stdout, proc.stderr
    lines: list[str] = []
    err = bytearray()
    killed = False

    async def read_stdout() -> None:
        async for raw in stdout:
            lines.append(raw.decode(errors="replace").rstrip("\n"))

    async def read_stderr() -> None:
        err.extend(await stderr.read())

    try:
        await asyncio.wait_for(asyncio.gather(read_stdout(), read_stderr(), proc.wait()), TIMEOUT_S)
    except TimeoutError:
        lines.append(spawn_error(f"timed out after {TIMEOUT_S}s"))
    finally:
        if proc.returncode is None:  # timed out or cancelled: never leave it running
            killed = True
            proc.kill()
            await proc.wait()
    if proc.returncode != 0 and not killed:
        tail = err.decode(errors="replace").strip()[-500:]
        lines.append(spawn_error(f"claude exit {proc.returncode}: {tail}"))
    return lines


class ClaudeCodeHarness:
    def __init__(
        self,
        model: str = "claude-opus-5-5",
        effort: str = "high",
        max_turns: int = 15,
        spawn: Spawn = spawn_claude,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_turns = max_turns
        self.spawn = spawn
        self.name = f"{AGENT}:{model}:{effort}"

    async def investigate(
        self, case: Case, *, events: EventLog, approve: bool, budget_usd: float
    ) -> RunResult[Diagnosis]:
        async with mcp_tools(build_telemetry_server(case.store)) as tools:
            gated = frozenset(t.name for t in tools if not t.read_only)
            allowed = allowed_tools(tools, approve=approve)
        run_id = events.start_run(scenario_id=case.scenario.id, agent=AGENT, provider=self.name)
        with tempfile.TemporaryDirectory(prefix="rootcause-") as tmp:
            root = Path(tmp)
            fixtures = stage_case(case, root / "fixtures")
            exe, *args = server_command(fixtures)
            config = root / "mcp.json"
            config.write_text(json.dumps({"mcpServers": {SERVER: {"command": exe, "args": args}}}))
            work = root / "work"
            work.mkdir()
            cmd = command(
                mcp_config=config,
                allowed=allowed,
                model=self.model,
                effort=self.effort,
                max_turns=self.max_turns,
                budget_usd=budget_usd,
            )
            lines = await self.spawn([*cmd, case.scenario.alert.render()], work)
        result = parse_stream(lines, run_id=run_id, gated=gated, approve=approve)
        for call in result.tool_calls:
            events.append(run_id, TOOL_CALL, {"call": call.model_dump(mode="json"), "result": ""})
        events.append(run_id, OUTCOME, result.model_dump(mode="json"))
        return result
