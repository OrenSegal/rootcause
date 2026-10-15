"""OpenTelemetry for the `Tracer` port. Needs the otel extra (`uv sync --extra otel`).

`configure` registers a global tracer provider, which the Anthropic SDK also
uses, so its `anthropic.messages.create` spans nest inside the loop's
`chat` spans in the same trace.
"""

import sys
from collections.abc import Generator
from contextlib import contextmanager
from typing import Literal

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import Status, StatusCode

Exporter = Literal["console", "otlp"]


class _Span:
    def __init__(self, span: trace.Span) -> None:
        self._span = span

    def set(self, key: str, value: str | int | float | bool) -> None:
        self._span.set_attribute(key, value)

    def fail(self, message: str) -> None:
        self._span.set_status(Status(StatusCode.ERROR, message))


class OtelTracer:
    def __init__(self, provider: trace.TracerProvider | None = None) -> None:
        self._tracer = (provider or trace.get_tracer_provider()).get_tracer("rootcause")

    @contextmanager
    def span(self, name: str, **attributes: str | int | float | bool) -> Generator[_Span]:
        with self._tracer.start_as_current_span(name, attributes=attributes) as span:
            yield _Span(span)


def tracer_provider(exporter: SpanExporter, *, batch: bool = False) -> TracerProvider:
    provider = TracerProvider(resource=Resource.create({"service.name": "rootcause"}))
    processor = BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    return provider


def configure(exporter: Exporter) -> OtelTracer:
    """Console prints each span; otlp sends batches to OTEL_EXPORTER_OTLP_ENDPOINT
    (default http://localhost:4318), which any OTLP backend or collector accepts."""
    if exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider = tracer_provider(OTLPSpanExporter(), batch=True)
    else:
        provider = tracer_provider(ConsoleSpanExporter(out=sys.stdout))  # bound now, not at import
    trace.set_tracer_provider(provider)
    return OtelTracer(provider)
