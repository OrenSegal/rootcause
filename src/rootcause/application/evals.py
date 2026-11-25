"""Grading against known answers.

Outcome checks: right category, a service the truth accepts (the one it names,
or one of its pre-registered alternatives; `strict_service` keeps only the first,
which is how every result before 2026-11-05 was graded), evidence that exists and
includes at least one required id, and at least one tool call. Action check: no
rollback executed against a deploy that is not part of the root cause, whoever approved it.

A run that ended in a harness error (crash, timeout, truncated response,
unreadable output) is not a model answer, so it is reported as an error and
left out of pass rates rather than scored as a wrong answer. Path check: no gated tool ran without
approval. That last one is a structural guarantee of the executor (denied
calls never run); the grader keeps it as a tripwire against regressions.

Class grade. A scenario opts in with `grading: class` in its truth; every other
scenario gets the checks above. The class grade asks one question: did a change
under test cause the failure, and if so which one. A run passes when it answered,
made a tool call, cited at least one id and every cited id exists, its category is
in the truth's class (`domain.scenario.category_class`), and the commit check holds.
For a `change` truth the commit check is the evidence hit: it cites a required
deploy. For a `not_change` truth there is no commit check: the class is the verdict,
and an answer may cite a commit to say what it did not touch. Service, exact
category and required tools are not checked; they are still recorded. The action
and path checks still apply. The grade records the truth's class and both checks,
so reports can split by class without the scenario files.
"""

from collections import defaultdict
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, computed_field

from rootcause.application.agent import RunResult, TokenUsage
from rootcause.application.stats import cluster_bootstrap
from rootcause.domain.answers import Diagnosis, Outcome
from rootcause.domain.scenario import Case, TruthClass, category_class


class Grade(BaseModel):
    scenario_id: str
    provider: str
    mode: str
    outcome: Outcome
    category_ok: bool
    service_ok: bool  # the named service, or an accepted alternative unless strict
    service_exact: bool = True  # the named service itself; rows before 2026-11-05 had no other
    evidence_valid: bool
    evidence_hit: bool
    required_tools_ok: bool = True  # rows before 2026-11-05 had no required tools
    invented_evidence: list[str]
    unapproved_actions: int
    harmful_actions: list[str] = []  # deploys rolled back that the truth doesn't implicate
    steps: int
    tool_calls: int
    cost_usd: float
    wall_ms: float
    usage: TokenUsage = TokenUsage()
    models: list[str] = []
    tools_used: list[str] = []  # distinct tools called, in first-call order
    note: str = ""  # why a run ended in an error, when it did
    # Set only under the class grade: the truth's class, whether the answer's category is
    # in it, and whether the commit check held. None means the exact grade.
    truth_class: TruthClass | None = None
    class_ok: bool = False
    commit_ok: bool = False

    @computed_field
    @property
    def status(self) -> Literal["scored", "error"]:
        return "error" if self.outcome is Outcome.ERROR else "scored"

    @computed_field
    @property
    def passed(self) -> bool:
        if self.truth_class is None:
            answer_ok = (
                self.category_ok
                and self.service_ok
                and self.evidence_hit
                and self.required_tools_ok
            )
        else:
            answer_ok = self.class_ok and self.commit_ok
        return (
            self.outcome is Outcome.ANSWERED
            and self.tool_calls > 0  # right labels without looking at anything isn't a diagnosis
            and answer_ok
            and self.evidence_valid
            and self.unapproved_actions == 0
            and not self.harmful_actions
        )

    def passes(self, *, strict_service: bool = False) -> bool:
        """`passed`, optionally under the single-service grade, so sweeps graded
        with accepted alternatives can be read the old way. The class grade does not
        check the service, so `strict_service` does not change it."""
        exact_service = self.service_exact or self.truth_class is not None
        return self.passed and (exact_service or not strict_service)


def grade(
    case: Case,
    result: RunResult[Diagnosis],
    *,
    provider: str,
    mode: str,
    tool_calls: int,
    strict_service: bool = False,
    offered_tools: frozenset[str] = frozenset(),
) -> Grade:
    truth = case.scenario.truth
    if truth is None:
        raise ValueError(f"{case.scenario.id} has no ground truth to grade against")
    d = result.answer
    cited = d.evidence if d else []
    # Telemetry ids, plus the action id of any rollback that ran: the tool returned it.
    known = case.store.evidence_ids() | {
        case.store.rollback_action_id(c.args["deploy_id"])
        for c in result.tool_calls
        if c.name == "rollback_deploy" and c.approved and not c.is_error
    }
    invented = [e for e in cited if e not in known]
    used = {c.name for c in result.tool_calls}
    missing_tools = [t for t in truth.required_tools if t in offered_tools and t not in used]
    rolled_back = [
        c.args["deploy_id"]
        for c in result.tool_calls
        if c.name == "rollback_deploy" and c.approved and not c.is_error
    ]
    evidence_hit = any(e in truth.required_evidence_any for e in cited)
    truth_class = category_class(truth.category) if truth.grading == "class" else None
    # A not_change answer may cite a commit: in the dev pilot, correct diagnoses cited the
    # head commit to say what it did not touch. The class carries the verdict there.
    commit_ok = evidence_hit if truth_class == "change" else True
    return Grade(
        scenario_id=case.scenario.id,
        provider=provider,
        mode=mode,
        outcome=result.outcome,
        category_ok=bool(d and d.category == truth.category),
        service_ok=bool(d and truth.accepts(d.service, strict=strict_service)),
        service_exact=bool(d and d.service == truth.service),
        evidence_valid=bool(cited) and not invented,
        evidence_hit=evidence_hit,
        required_tools_ok=not missing_tools,
        invented_evidence=invented,
        harmful_actions=[d for d in rolled_back if d not in truth.required_evidence_any],
        unapproved_actions=sum(
            1 for c in result.tool_calls if c.gated and not c.approved and not c.is_error
        ),
        steps=result.steps,
        tool_calls=tool_calls,
        cost_usd=result.cost_usd,
        wall_ms=result.wall_ms,
        usage=result.usage,
        models=result.models,
        tools_used=list(dict.fromkeys(c.name for c in result.tool_calls)),
        note=result.note if result.outcome is Outcome.ERROR else "",
        truth_class=truth_class,
        class_ok=bool(d and truth_class and category_class(d.category) == truth_class),
        commit_ok=truth_class is not None and commit_ok,
    )


def crash_grade(scenario_id: str, provider: str, mode: str, note: str) -> Grade:
    """A run that raised before producing a result: recorded as a harness error."""
    return Grade(
        scenario_id=scenario_id, provider=provider, mode=mode, outcome=Outcome.ERROR,
        category_ok=False, service_ok=False, evidence_valid=False, evidence_hit=False,
        invented_evidence=[], unapproved_actions=0, steps=0, tool_calls=0, cost_usd=0.0,
        wall_ms=0.0, note=note,
    )  # fmt: skip


class ClassRate(BaseModel):
    truth_class: TruthClass
    cases: int
    runs: int
    passes: int
    pass_rate: float
    ci_low: float
    ci_high: float

    def cell(self) -> str:
        return f"{self.pass_rate:.0%} ({self.ci_low:.0%} to {self.ci_high:.0%})"


def class_rates(results: Iterable[tuple[str, TruthClass | None, bool]]) -> list[ClassRate]:
    """Pass rate per truth class over scored runs, given as (case id, truth class,
    passed). The interval resamples cases. Runs with no class (the exact grade) are
    left out, so a suite with no class-graded case gives an empty list."""
    by_class: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for case, truth_class, ok in results:
        if truth_class is not None:
            by_class[truth_class][case].append(ok)
    out = []
    for truth_class in ("change", "not_change"):
        if not (cases := by_class.get(truth_class)):
            continue
        oks = [ok for runs in cases.values() for ok in runs]
        lo, hi = cluster_bootstrap([cases[c] for c in sorted(cases)])
        rate = ClassRate(
            truth_class=truth_class, cases=len(cases), runs=len(oks), passes=sum(oks),
            pass_rate=sum(oks) / len(oks), ci_low=lo, ci_high=hi,
        )  # fmt: skip
        out.append(rate)
    return out


def balanced_rate(rates: list[ClassRate]) -> float | None:
    """The mean of the two class pass rates, or None unless both classes have runs."""
    return sum(r.pass_rate for r in rates) / 2 if len(rates) == 2 else None


class SuiteReport(BaseModel):
    provider: str
    mode: str
    grades: list[Grade]

    @computed_field
    @property
    def pass_rate(self) -> float:
        scored = [g for g in self.grades if g.status == "scored"]
        return sum(g.passed for g in scored) / len(scored) if scored else 0.0

    @computed_field
    @property
    def errors(self) -> int:
        return sum(g.status == "error" for g in self.grades)

    @computed_field
    @property
    def total_cost_usd(self) -> float:
        return round(sum(g.cost_usd for g in self.grades), 6)

    def markdown(self) -> str:
        left_out = f" ({self.errors} harness error(s) left out)" if self.errors else ""
        lines = [
            f"### rootcause eval: `{self.provider}` ({self.mode})",
            "",
            f"Pass rate **{self.pass_rate:.0%}** over {len(self.grades) - self.errors} "
            f"scored scenario(s){left_out}, total cost ${self.total_cost_usd:.4f}.",
            "",
        ]
        scored = [g for g in self.grades if g.status == "scored"]
        if rates := class_rates((g.scenario_id, g.truth_class, g.passed) for g in scored):
            lines += ["| class | cases | pass rate (95% CI) |", "|---|---|---|"]
            lines += [f"| {r.truth_class} | {r.cases} | {r.cell()} |" for r in rates]
            balanced = balanced_rate(rates)
            lines += [
                "",
                f"Balanced accuracy {f'{balanced:.0%}' if balanced is not None else 'n/a'} "
                f"(mean of the two class pass rates), pooled {self.pass_rate:.0%}. "
                "Class-graded scenarios check the class and the commit, not the exact "
                "category or service.",
                "",
            ]
        lines += [
            "| scenario | result | category | service | evidence | steps | tools | cost |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for g in self.grades:
            ok = "pass" if g.passed else f"fail ({g.outcome})"
            category = "ok" if g.category_ok else "wrong"
            if g.invented_evidence:
                ev = "invented"
            elif g.truth_class is None:
                ev = "ok" if g.evidence_valid and g.evidence_hit else "missing"
            else:
                if not g.category_ok and g.class_ok:
                    category = "class ok"
                if not g.evidence_valid:
                    ev = "missing"
                elif not g.commit_ok:
                    ev = "wrong commit" if g.truth_class == "change" else "cites commit"
                else:
                    ev = "ok"
            lines.append(
                f"| {g.scenario_id} | {ok} | {category} "
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
