"""Span names and attributes for agent runs, after the OpenTelemetry GenAI
semantic conventions: `invoke_agent {agent}`, `chat {model}`, `execute_tool {tool}`,
with `gen_ai.*` attributes where a convention exists and `rootcause.*` where it
doesn't. `NullTracer` is the default, so tracing costs nothing unless the otel
extra is installed and a tracer is configured.

Token attributes follow the convention that `gen_ai.usage.input_tokens` includes
cache reads and writes, which are also given on their own.
"""

from collections.abc import Generator
from contextlib import contextmanager

from anthropic.types.beta import BetaUsage

OPERATION = "gen_ai.operation.name"
PROVIDER = "gen_ai.provider.name"
AGENT = "gen_ai.agent.name"
REQUEST_MODEL = "gen_ai.request.model"
RESPONSE_MODEL = "gen_ai.response.model"
TOOL = "gen_ai.tool.name"
TOOL_CALL_ID = "gen_ai.tool.call.id"


def usage_attributes(usage: BetaUsage) -> dict[str, int]:
    read = usage.cache_read_input_tokens or 0
    write = usage.cache_creation_input_tokens or 0
    return {
        "gen_ai.usage.input_tokens": usage.input_tokens + read + write,
        "gen_ai.usage.output_tokens": usage.output_tokens,
        "gen_ai.usage.cache_read.input_tokens": read,
        "gen_ai.usage.cache_write.input_tokens": write,
    }


class _NullSpan:
    def set(self, key: str, value: str | int | float | bool) -> None:
        pass

    def fail(self, message: str) -> None:
        pass


class NullTracer:
    @contextmanager
    def span(self, name: str, **attributes: str | int | float | bool) -> Generator[_NullSpan]:
        yield _NullSpan()
