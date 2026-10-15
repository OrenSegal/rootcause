"""Claude Code as a completion endpoint for the hand-written loop.

Each `complete()` is one headless `claude -p` call with no tools of its own: the
transcript goes in on stdin, the tool catalog and the brief go in the system
prompt, and the reply comes back as structured output naming the tool calls to
make next. The loop then runs those calls through its own executor, approval
gate, grounding check and orchestrator, exactly as with the Messages API.

That makes the hand-written loop runnable on a Claude subscription. What it does
not exercise is the Anthropic adapter's own request path: here tool calls are
emulated through structured output rather than native `tool_use`, thinking blocks
are not returned, and caching is whatever Claude Code applies. Wall time includes
starting a `claude` process for every turn.
"""

import asyncio
import json
import tempfile
from collections.abc import Awaitable, Callable
from itertools import count
from pathlib import Path
from typing import Any

from anthropic.types.beta import BetaMessage

from rootcause.application.ports import Block, Message

TIMEOUT_S = 600
PROTOCOL = """\
You are the model behind an agent loop. You cannot run tools yourself. Each reply
names the tool calls the loop should make next; it runs them and sends you the
results. Put each call's arguments in input_json as a JSON object that matches the
tool's input schema. Independent calls can go in one reply. To finish, call the
tool that submits the answer. Tools available:
"""

Spawn = Callable[[list[str], str], Awaitable[str]]
_ids = count(1)


class CompletionError(Exception):
    """Claude Code did not return a usable reply. The loop records it as a harness error."""


def completion_schema(tool_names: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Brief reasoning for this step."},
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": tool_names},
                        "input_json": {"type": "string"},
                    },
                    "required": ["name", "input_json"],
                },
            },
        },
        "required": ["text", "tool_calls"],
    }


def render_tools(tools: list[Block]) -> str:
    return "\n".join(
        f"- {t['name']}: {t.get('description', '').strip()}\n  input schema: "
        f"{json.dumps(t.get('input_schema', {}), separators=(',', ':'))}"
        for t in tools
    )


def _render_block(block: Block) -> str:
    kind = block.get("type")
    if kind == "text":
        return block["text"]
    if kind == "tool_use":
        return f"CALL {block['name']} [{block['id']}] {json.dumps(block.get('input', {}))}"
    if kind == "tool_result":
        status = "ERROR" if block.get("is_error") else "RESULT"
        content = block.get("content")
        text = content if isinstance(content, str) else json.dumps(content)
        return f"{status} [{block['tool_use_id']}] {text}"
    return ""


def render_transcript(messages: list[Message]) -> str:
    parts = []
    for m in messages:
        content = m["content"]
        body = content if isinstance(content, str) else "\n".join(map(_render_block, content))
        parts.append(f"## {m['role'].upper()}\n{body}")
    return "\n\n".join(parts) + "\n\n## Your next step"


async def spawn_claude(cmd: list[str], stdin: str) -> str:
    with tempfile.TemporaryDirectory(prefix="rootcause-turn-") as empty:  # no CLAUDE.md to pick up
        return await _spawn_in(cmd, stdin, Path(empty))


async def _spawn_in(cmd: list[str], stdin: str, cwd: Path) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin.encode()), TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise CompletionError(f"claude timed out after {TIMEOUT_S}s") from None
    if proc.returncode != 0 and not out:
        raise CompletionError(f"claude exit {proc.returncode}: {err.decode()[-500:]}")
    return out.decode()


class ClaudeCodeCompletion:
    def __init__(
        self, model: str = "claude-opus-5-5", effort: str = "high", spawn: Spawn = spawn_claude
    ) -> None:
        self.model = model
        self.effort = effort
        self.spawn = spawn
        self.name = f"claude-code-loop:{model}:{effort}"

    async def complete(
        self, *, system: list[Block], messages: list[Message], tools: list[Block]
    ) -> BetaMessage:
        brief = "\n\n".join(b["text"] for b in system if b.get("type") == "text")
        cmd = [
            "claude", "-p",
            "--system-prompt", f"{brief}\n\n{PROTOCOL}{render_tools(tools)}",
            "--model", self.model,
            "--effort", self.effort,
            "--tools", "",
            "--setting-sources", "",
            "--strict-mcp-config",
            "--json-schema", json.dumps(completion_schema([t["name"] for t in tools])),
            "--output-format", "json",
            "--no-session-persistence",
        ]  # fmt: skip
        raw = await self.spawn(cmd, render_transcript(messages))
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            raise CompletionError(f"unreadable output: {raw[:200]!r}") from None
        reply = result.get("structured_output")
        if result.get("is_error") or not isinstance(reply, dict):
            raise CompletionError(f"no structured reply: {str(result.get('result', ''))[:300]}")
        return self._message(reply, result)

    def _message(self, reply: dict[str, Any], result: dict[str, Any]) -> BetaMessage:
        content: list[Block] = []
        if text := reply.get("text"):
            content.append({"type": "text", "text": text})
        for c in reply.get("tool_calls") or []:
            try:
                args = json.loads(c.get("input_json") or "{}")
            except json.JSONDecodeError:
                args = None
            # Arguments that aren't a JSON object go through as-is, so the tool's own
            # validation error reaches the model, as a malformed native call would.
            if not isinstance(args, dict):
                args = {"input_json": c.get("input_json")}
            content.append(
                {
                    "type": "tool_use",
                    "id": f"toolu_cc{next(_ids)}",
                    "name": c["name"],
                    "input": args,
                }
            )
        usage = result.get("usage") or {}
        served = next(iter(result.get("modelUsage") or {}), self.model)
        return BetaMessage.model_validate(
            {
                "id": f"msg_cc{next(_ids)}",
                "type": "message",
                "role": "assistant",
                "model": served,
                "content": content,
                "stop_reason": "tool_use"
                if any(b["type"] == "tool_use" for b in content)
                else "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": usage.get("input_tokens") or 0,
                    "output_tokens": usage.get("output_tokens") or 0,
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens") or 0,
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens") or 0,
                },
            }
        )
