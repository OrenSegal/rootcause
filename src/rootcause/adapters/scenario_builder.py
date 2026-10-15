"""Authoring DSL for scenario fixtures. Scenarios are written as code in
`scripts/author_scenarios.py`; this writes them out in the format
`fixtures.load_case` reads. Output is deterministic, so a test can regenerate
every scenario and fail if the committed fixtures have drifted."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from rootcause.adapters.fixtures import write_case
from rootcause.domain.scenario import Alert, Case, Category, GroundTruth, Scenario
from rootcause.domain.store import TelemetryStore
from rootcause.domain.telemetry import (
    ConfigChange,
    Deploy,
    LogLevel,
    LogLine,
    MetricPoint,
    MetricSeries,
    Span,
    SpanStatus,
)

T0 = datetime(2026, 10, 14, 13, 0, tzinfo=UTC)


class ScenarioBuilder:
    def __init__(
        self,
        id: str,
        title: str,
        alert: Alert,
        approval: Literal["approve", "deny"] = "deny",
        t0: datetime = T0,
    ) -> None:
        self.id, self.title, self.alert, self.approval, self.t0 = id, title, alert, approval, t0
        self.logs: list[LogLine] = []
        self.metrics: list[MetricSeries] = []
        self.spans: list[Span] = []
        self.deploys: list[Deploy] = []
        self._truth: GroundTruth | None = None

    def at(self, minute: float) -> datetime:
        return self.t0 + timedelta(minutes=minute)

    def deploy(
        self,
        id: str,
        service: str,
        version: str,
        minute: float,
        summary: str,
        author: str = "dev-bot",
        before: dict[str, str] | None = None,
        after: dict[str, str] | None = None,
    ) -> str:
        self.deploys.append(
            Deploy(
                id=id,
                service=service,
                version=version,
                ts=self.at(minute),
                author=author,
                summary=summary,
                config_before=before or {},
                config_after=after or {},
            )
        )
        return id

    def config_id(self, deploy_id: str, key: str) -> str:
        return ConfigChange.make_id(deploy_id, key)

    def log(
        self,
        minute: float,
        service: str,
        level: LogLevel,
        message: str,
        trace_id: str | None = None,
    ) -> str:
        log_id = f"log-{len(self.logs) + 1:04d}"
        self.logs.append(
            LogLine(
                id=log_id,
                ts=self.at(minute),
                service=service,
                level=level,
                message=message,
                trace_id=trace_id,
            )
        )
        return log_id

    def step_series(
        self,
        id: str,
        service: str,
        name: str,
        unit: str,
        before: float,
        after: float,
        onset: float,
        span: tuple[int, int, int] = (0, 60, 2),
    ) -> str:
        start, end, every = span
        return self.series(
            id,
            service,
            name,
            unit,
            {m: before if m < onset else after for m in range(start, end, every)},
        )

    def series(self, id: str, service: str, name: str, unit: str, values: dict[int, float]) -> str:
        points = [MetricPoint(ts=self.at(m), value=v) for m, v in sorted(values.items())]
        self.metrics.append(
            MetricSeries(id=id, service=service, name=name, unit=unit, points=points)
        )
        return id

    def span(
        self,
        span_id: str,
        trace_id: str,
        service: str,
        name: str,
        minute: float,
        duration_ms: float,
        status: SpanStatus = "ok",
        parent_id: str | None = None,
    ) -> str:
        self.spans.append(
            Span(
                span_id=span_id,
                trace_id=trace_id,
                parent_id=parent_id,
                service=service,
                name=name,
                start=self.at(minute),
                duration_ms=duration_ms,
                status=status,
            )
        )
        return span_id

    def truth(self, category: Category, service: str, *evidence: str) -> None:
        self._truth = GroundTruth(
            category=category, service=service, required_evidence_any=list(evidence)
        )

    def scenario(self) -> Scenario:
        if self._truth is None:
            raise ValueError(f"{self.id}: call truth() before writing")
        return Scenario(
            id=self.id,
            title=self.title,
            alert=self.alert,
            approval=self.approval,
            truth=self._truth,
        )

    def write(self, root: Path) -> Path:
        store = TelemetryStore(self.logs, self.metrics, self.spans, self.deploys)
        return write_case(Case(self.scenario(), store), root / self.id)
