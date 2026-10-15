"""Grading against known answers.

Outcome checks: right category, right service, evidence that exists and
includes at least one required id. Path check: no gated tool ran without
approval. That last one is a structural guarantee of the executor (denied
calls never run); the grader keeps it as a tripwire against regressions.
"""

from pydantic import BaseModel, computed_field

from rootcause.application.agent import RunResult
from rootcause.domain.answers import Diagnosis, Outcome
from rootcause.domain.scenario import Case


class Grade(BaseModel):
    scenario_id: str
    provider: str
    mode: str
    outcome: Outcome
    category_ok: bool
    service_ok: bool
    evidence_valid: bool
    evidence_hit: bool
    invented_evidence: list[str]
    unapproved_actions: int
    steps: int
    tool_calls: int
    cost_usd: float
    wall_ms: float

    @computed_field
    @property
    def passed(self) -> bool:
        return (
            self.outcome is Outcome.ANSWERED
            and self.category_ok
            and self.service_ok
            and self.evidence_valid
            and self.evidence_hit
            and self.unapproved_actions == 0
        )


def grade(
    case: Case, result: RunResult[Diagnosis], *, provider: str, mode: str, tool_calls: int
) -> Grade:
    truth = case.scenario.truth
    if truth is None:
        raise ValueError(f"{case.scenario.id} has no ground truth to grade against")
    d = result.answer
    cited = d.evidence if d else []
    known = case.store.evidence_ids()
    invented = [e for e in cited if e not in known]
    return Grade(
        scenario_id=case.scenario.id,
        provider=provider,
        mode=mode,
        outcome=result.outcome,
        category_ok=bool(d and d.category == truth.category),
        service_ok=bool(d and d.service == truth.service),
        evidence_valid=bool(cited) and not invented,
        evidence_hit=any(e in truth.required_evidence_any for e in cited),
        invented_evidence=invented,
        unapproved_actions=sum(
            1 for c in result.tool_calls if c.gated and not c.approved and not c.is_error
        ),
        steps=result.steps,
        tool_calls=tool_calls,
        cost_usd=result.cost_usd,
        wall_ms=result.wall_ms,
    )


class SuiteReport(BaseModel):
    provider: str
    mode: str
    grades: list[Grade]

    @computed_field
    @property
    def pass_rate(self) -> float:
        return sum(g.passed for g in self.grades) / len(self.grades) if self.grades else 0.0

    @computed_field
    @property
    def total_cost_usd(self) -> float:
        return round(sum(g.cost_usd for g in self.grades), 6)

    def markdown(self) -> str:
        lines = [
            f"### rootcause eval: `{self.provider}` ({self.mode})",
            "",
            f"Pass rate **{self.pass_rate:.0%}** over {len(self.grades)} scenario(s), "
            f"total cost ${self.total_cost_usd:.4f}.",
            "",
            "| scenario | result | category | service | evidence | steps | tools | cost |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for g in self.grades:
            ok = "pass" if g.passed else f"fail ({g.outcome})"
            if g.invented_evidence:
                ev = "invented"
            else:
                ev = "ok" if g.evidence_valid and g.evidence_hit else "missing"
            lines.append(
                f"| {g.scenario_id} | {ok} | {'ok' if g.category_ok else 'wrong'} "
                f"| {'ok' if g.service_ok else 'wrong'} | {ev} | {g.steps} | {g.tool_calls} "
                f"| ${g.cost_usd:.4f} |"
            )
        return "\n".join(lines) + "\n"


def comparison_markdown(reports: list[SuiteReport], notes: list[str]) -> str:
    lines = [
        "| provider | mode | pass rate | cost | avg steps | avg tool calls | avg wall |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        n = len(r.grades) or 1
        lines.append(
            f"| {r.provider} | {r.mode} | {r.pass_rate:.0%} | ${r.total_cost_usd:.4f} "
            f"| {sum(g.steps for g in r.grades) / n:.1f} "
            f"| {sum(g.tool_calls for g in r.grades) / n:.1f} "
            f"| {sum(g.wall_ms for g in r.grades) / n / 1000:.1f}s |"
        )
    return "\n".join(lines + [""] + [f"- {note}" for note in notes]) + "\n"
