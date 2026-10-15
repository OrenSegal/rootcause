"""What an investigator can observe. Every citable record carries a stable id, and
graders check that cited ids exist, so a diagnosis can't point at evidence that
was never returned."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

LogLevel = Literal["debug", "info", "warn", "error"]
SpanStatus = Literal["ok", "error"]


class LogLine(BaseModel):
    id: str
    ts: datetime
    service: str
    level: LogLevel
    message: str
    trace_id: str | None = None


class MetricPoint(BaseModel):
    ts: datetime
    value: float


class MetricSeries(BaseModel):
    id: str
    service: str
    name: str
    unit: str
    points: list[MetricPoint]


class Span(BaseModel):
    span_id: str
    trace_id: str
    parent_id: str | None = None
    service: str
    name: str
    start: datetime
    duration_ms: float
    status: SpanStatus = "ok"


class Deploy(BaseModel):
    """A change that shipped: a service deploy, or a commit in the CI adapter."""

    id: str
    service: str
    version: str
    ts: datetime
    author: str
    summary: str
    config_before: dict[str, str] = {}
    config_after: dict[str, str] = {}
    files: list[str] = []


class ConfigChange(BaseModel):
    id: str
    deploy_id: str
    key: str
    before: str | None
    after: str | None

    @staticmethod
    def make_id(deploy_id: str, key: str) -> str:
        return f"cfg:{deploy_id}:{key}"
