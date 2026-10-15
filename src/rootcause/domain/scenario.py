import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from rootcause.domain.store import TelemetryStore


class Category(StrEnum):
    CONFIG_CHANGE = "config_change"
    BAD_CODE_DEPLOY = "bad_code_deploy"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    DEPENDENCY_FAILURE = "dependency_failure"
    INFRASTRUCTURE = "infrastructure"
    NO_INCIDENT = "no_incident"


class Alert(BaseModel):
    """The page that starts an investigation. `render` is the only definition of
    the text an investigator receives, and `parse` reads it back."""

    model_config = ConfigDict(extra="forbid")

    severity: str = "sev2"
    service: str
    summary: str

    PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"^ALERT (?P<severity>\S+) service=(?P<service>[\w-]+): (?P<summary>.*)$", re.DOTALL
    )

    def render(self) -> str:
        return f"ALERT {self.severity} service={self.service}: {self.summary}"

    @classmethod
    def parse(cls, text: str) -> Alert | None:
        match = cls.PATTERN.match(text.strip())
        return cls(**match.groupdict()) if match else None


class GroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    service: str
    # A diagnosis must cite at least one of these to count as evidence-backed.
    required_evidence_any: list[str]


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    alert: Alert
    # What the simulated operator answers when the agent asks to run a gated tool.
    approval: Literal["approve", "deny"] = "deny"
    # Known root cause. Absent for a recorded run nobody has diagnosed yet.
    truth: GroundTruth | None = None
    # Where the fixtures came from: "authored" (scripts/author_scenarios.py) or "recorded".
    provenance: Literal["authored", "recorded"] = "authored"


@dataclass
class Case:
    """A scenario paired with the world it describes."""

    scenario: Scenario
    store: TelemetryStore

    def unknown_truth_evidence(self) -> list[str]:
        if self.scenario.truth is None:
            return []
        known = self.store.evidence_ids()
        return [e for e in self.scenario.truth.required_evidence_any if e not in known]
