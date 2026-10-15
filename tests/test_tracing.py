"""Traces of agent runs: span names and attributes follow the OpenTelemetry GenAI
conventions, with the loop tested against a recording tracer and the OpenTelemetry
adapter tested against the SDK's in-memory exporter (skipped without the otel extra)."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import pytest
from conftest import Scripted, call, make_loop

from rootcause.adapters.mcp_telemetry import build_telemetry_server
from rootcause.adapters.mcp_tools import mcp_tools
from rootcause.application.investigator import investigator_spec

ANSWER = {
    "category": "config_change", "service": "checkout", "summary": "s",
    "evidence": ["d-1042"], "recommended_action": "a", "confidence": 0.8,
}  # fmt: skip

_current: ContextVar[RecordedSpan | None] = ContextVar("span", default=None)


@dataclass
class RecordedSpan:
    name: str
    attributes: dict[str, Any]
    parent: str | None
    error: str | None = None

    def set(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def fail(self, message: str) -> None:
        self.error = message


@dataclass
class RecordingTracer:
    spans: list[RecordedSpan] = field(default_factory=list)

    @contextmanager
    def span(self, name: str, **attributes: Any):
        parent = _current.get()
        s = RecordedSpan(name, dict(attributes), parent.name if parent else None)
        self.spans.append(s)
        token = _current.set(s)
        try:
            yield s
        finally:
            _current.reset(token)

    def named(self, name: str) -> list[RecordedSpan]:
        return [s for s in self.spans if s.name == name]


async def traced_run(case, tracer):
    provider = Scripted(
        [
            {"content": [call("list_deploys", {}, "a")]},
            {"content": [call("submit_diagnosis", ANSWER, "b")]},
        ]
    )
    loop = make_loop(provider)
    loop.tracer = tracer
    async with mcp_tools(build_telemetry_server(case.store)) as tools:
        return await loop.run(
            investigator_spec(), case.scenario.alert.render(), tools, scenario_id="s"
        )


async def test_a_run_is_one_agent_span_with_model_and_tool_spans_inside(case):
    tracer = RecordingTracer()
    result = await traced_run(case, tracer)

    (agent,) = tracer.named("invoke_agent investigator")
    assert agent.parent is None
    assert agent.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert agent.attributes["rootcause.outcome"] == "answered"
    assert agent.attributes["rootcause.run_id"] == result.run_id

    chats = tracer.named("chat claude-opus-5-5")
    assert len(chats) == 2 and all(c.parent == agent.name for c in chats)
    assert chats[0].attributes["gen_ai.usage.output_tokens"] == 200
    assert chats[0].attributes["gen_ai.response.model"] == "claude-opus-5-5"

    (tool,) = tracer.named("execute_tool list_deploys")
    assert tool.parent == agent.name
    assert tool.attributes["gen_ai.tool.name"] == "list_deploys"
    assert tool.attributes["gen_ai.tool.call.id"] == "a"
    assert tool.attributes["rootcause.tool.gated"] is False


async def test_claude_code_runs_are_traced_from_the_stream(case):
    from pathlib import Path

    from rootcause.adapters.claude_code import ClaudeCodeHarness
    from rootcause.adapters.fixtures import load_case
    from rootcause.adapters.sqlite_events import SqliteEventLog

    fixture = Path(__file__).parent / "fixtures" / "claude_code_005_denied_rollback.jsonl"

    async def spawn(cmd, cwd):
        return fixture.read_text().splitlines()

    tracer = RecordingTracer()
    harness = ClaudeCodeHarness(spawn=spawn, tracer=tracer)
    scenario = load_case(Path(__file__).parent.parent / "scenarios" / "005-inventory-bad-deploy")
    await harness.investigate(scenario, events=SqliteEventLog(), approve=False, budget_usd=0.5)

    (agent,) = tracer.named("invoke_agent claude-code")
    assert agent.attributes["rootcause.reconstructed"] is True
    assert agent.attributes["gen_ai.usage.output_tokens"] == 1117
    rollback = tracer.named("execute_tool rollback_deploy")
    assert rollback and rollback[0].parent == agent.name
    assert rollback[0].error is not None  # the operator denied it


async def test_opentelemetry_adapter_exports_genai_spans(case):
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from rootcause.adapters.otel import OtelTracer, tracer_provider

    exporter = InMemorySpanExporter()
    tracer = OtelTracer(tracer_provider(exporter))
    await traced_run(case, tracer)
    spans = {s.name: s for s in exporter.get_finished_spans()}
    agent = spans["invoke_agent investigator"]
    tool = spans["execute_tool list_deploys"]
    assert tool.parent is not None and agent.context is not None
    assert tool.parent.span_id == agent.context.span_id
    chat = spans["chat claude-opus-5-5"].attributes
    assert chat is not None and chat["gen_ai.usage.output_tokens"] == 200


def test_cli_trace_option_prints_spans(scenario_dir, tmp_path):
    pytest.importorskip("opentelemetry.sdk")
    from typer.testing import CliRunner

    from rootcause.interfaces.cli import app

    runner = CliRunner(env={"NO_COLOR": "1", "COLUMNS": "200"})
    out = runner.invoke(
        app,
        ["--trace", "console", "investigate", str(scenario_dir), "--runlog", str(tmp_path / "r")],
    )
    assert out.exit_code == 0, out.output
    assert '"name": "invoke_agent investigator"' in out.output
    assert '"name": "execute_tool list_deploys"' in out.output
