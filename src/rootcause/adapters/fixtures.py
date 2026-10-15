"""Reads and writes scenario directories. `write_case` is the inverse of
`load_case`, and the only place the on-disk format is written."""

import json
from pathlib import Path

import yaml
from pydantic import BaseModel, TypeAdapter

from rootcause.application.sweep import SweepSpec
from rootcause.domain.scenario import Case, Scenario
from rootcause.domain.store import TelemetryStore
from rootcause.domain.telemetry import Deploy, LogLine, MetricSeries, Span

SCENARIO_FILE = "scenario.yaml"
LOGS, METRICS, TRACES, DEPLOYS = "logs.jsonl", "metrics.json", "traces.json", "deploys.json"

_metrics = TypeAdapter(list[MetricSeries])
_spans = TypeAdapter(list[Span])
_deploys = TypeAdapter(list[Deploy])


def load_scenario(path: Path) -> Scenario:
    return Scenario.model_validate(yaml.safe_load((path / SCENARIO_FILE).read_text()))


def load_case(path: Path) -> Case:
    logs = [
        LogLine.model_validate_json(line)
        for line in (path / LOGS).read_text().splitlines()
        if line.strip()
    ]
    store = TelemetryStore(
        logs=logs,
        metrics=_metrics.validate_json((path / METRICS).read_bytes()),
        spans=_spans.validate_json((path / TRACES).read_bytes()),
        deploys=_deploys.validate_json((path / DEPLOYS).read_bytes()),
    )
    return Case(scenario=load_scenario(path), store=store)


def discover(root: Path) -> list[Path]:
    return sorted(p.parent for p in root.glob(f"*/{SCENARIO_FILE}"))


def _dump(models: list[BaseModel]) -> str:
    return json.dumps([m.model_dump(mode="json") for m in models], indent=2) + "\n"


def write_case(case: Case, out: Path, *, scenario_file: bool = True) -> Path:
    """Deterministic output, so committed fixtures can be checked for drift."""
    out.mkdir(parents=True, exist_ok=True)
    if scenario_file:
        scenario = case.scenario.model_dump(mode="json", exclude_defaults=True)
        (out / SCENARIO_FILE).write_text(yaml.safe_dump(scenario, sort_keys=False, width=88))
    store = case.store
    (out / LOGS).write_text("".join(log.model_dump_json() + "\n" for log in store.logs))
    (out / METRICS).write_text(_dump(list(store.metrics)))
    (out / TRACES).write_text(_dump(list(store.spans)))
    (out / DEPLOYS).write_text(_dump(list(store.deploys)))
    return out


def load_sweep_spec(path: Path) -> SweepSpec:
    return SweepSpec.model_validate(yaml.safe_load(path.read_text()))
