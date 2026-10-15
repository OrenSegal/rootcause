"""The simulated world for one scenario: queries over its telemetry, plus the one
change an agent can make to it (rolling back a deploy). No IO here; loading
from disk lives in `adapters/fixtures.py`."""

from datetime import datetime

from rootcause.domain.telemetry import ConfigChange, Deploy, LogLine, MetricSeries, Span


class TelemetryStore:
    def __init__(
        self,
        logs: list[LogLine],
        metrics: list[MetricSeries],
        spans: list[Span],
        deploys: list[Deploy],
    ) -> None:
        self.logs = sorted(logs, key=lambda log: log.ts)
        self.metrics = metrics
        self.spans = spans
        self.deploys = sorted(deploys, key=lambda d: d.ts)
        # deploy_id -> rollback action id. Keyed by deploy so a repeat is a no-op.
        self.rollbacks: dict[str, str] = {}

    def query_logs(
        self,
        service: str | None = None,
        level: str | None = None,
        contains: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
    ) -> list[LogLine]:
        out = [
            log
            for log in self.logs
            if (service is None or log.service == service)
            and (level is None or log.level == level)
            and (contains is None or contains.lower() in log.message.lower())
            and (since is None or log.ts >= since)
            and (until is None or log.ts <= until)
        ]
        return out[:limit]

    def query_metrics(self, service: str, name: str | None = None) -> list[MetricSeries]:
        return [
            m for m in self.metrics if m.service == service and (name is None or m.name == name)
        ]

    def search_traces(
        self, service: str, min_duration_ms: float = 0, status: str | None = None, limit: int = 10
    ) -> list[Span]:
        roots = [
            s
            for s in self.spans
            if s.service == service
            and s.parent_id is None
            and s.duration_ms >= min_duration_ms
            and (status is None or s.status == status)
        ]
        return sorted(roots, key=lambda s: -s.duration_ms)[:limit]

    def get_trace(self, trace_id: str) -> list[Span]:
        return sorted((s for s in self.spans if s.trace_id == trace_id), key=lambda s: s.start)

    def list_deploys(
        self,
        service: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Deploy]:
        return [
            d
            for d in self.deploys
            if (service is None or d.service == service)
            and (since is None or d.ts >= since)
            and (until is None or d.ts <= until)
        ]

    def get_deploy(self, deploy_id: str) -> Deploy | None:
        return next((d for d in self.deploys if d.id == deploy_id), None)

    def diff_config(self, deploy_id: str) -> list[ConfigChange]:
        deploy = self.get_deploy(deploy_id)
        if deploy is None:
            return []
        keys = sorted(set(deploy.config_before) | set(deploy.config_after))
        return [
            ConfigChange(
                id=ConfigChange.make_id(deploy.id, key),
                deploy_id=deploy.id,
                key=key,
                before=deploy.config_before.get(key),
                after=deploy.config_after.get(key),
            )
            for key in keys
            if deploy.config_before.get(key) != deploy.config_after.get(key)
        ]

    def rollback(self, deploy_id: str) -> tuple[str, bool]:
        """Returns (action_id, created). Idempotent per deploy."""
        if deploy_id in self.rollbacks:
            return self.rollbacks[deploy_id], False
        action_id = f"rb-{deploy_id}"
        self.rollbacks[deploy_id] = action_id
        return action_id, True

    def evidence_ids(self) -> set[str]:
        ids: set[str] = set()
        ids.update(log.id for log in self.logs)
        ids.update(m.id for m in self.metrics)
        ids.update(s.span_id for s in self.spans)
        ids.update(s.trace_id for s in self.spans)
        for deploy in self.deploys:
            ids.add(deploy.id)
            ids.update(change.id for change in self.diff_config(deploy.id))
        return ids
