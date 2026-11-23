"""A triage ladder replayed from finished runs: free rules first, a cheap model on
what they leave, and a strong model only where a lint flags the cheap answer.

No model is called. The rules' answer is the same for every repeat; repeat i of the
cheap model pairs with repeat i of the strong model. A case and repeat is left out
when either model has no scored run for it (missing, or a harness error), so the
ladder and each model alone cover the same runs. An escalated run costs the cheap
run plus the strong run. Which lints escalate is fixed here, chosen on the dev split.
"""

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

from rootcause.application.diff import RunRecord
from rootcause.application.evals import balanced_rate, class_rates
from rootcause.domain.scenario import TruthClass

# Chosen on benchmarks/ci/dev, ci-dev-pilot-2026-11-23, Haiku as the cheap model: on
# the runs the rules left, it escalated 3 answers and the ladder matched the best
# accuracy any set reached. blamed_commit_unnamed matched it with 11 escalations;
# low_confidence alone fixed nothing; the other lints never fired there.
ESCALATE = frozenset({"change_without_commit"})
NO_ANSWER = "no_answer"  # the cheap run gave no answer: always escalated

Rung = Literal["rules", "cheap", "strong"]


class Attempt(BaseModel):
    """One scored run: whether it passed, what it cost, whether it answered, and the
    lints that flag it."""

    passed: bool
    cost: float
    answered: bool
    flags: list[str] = []


class Step(BaseModel):
    """The ladder's outcome for one case and repeat."""

    case: str
    rep: int
    truth_class: TruthClass | None
    rung: Rung
    passed: bool
    cost: float
    answered: bool
    triggers: list[str] = []  # why the cheap answer was escalated

    def record(self) -> RunRecord:
        return RunRecord(
            case=self.case, passed=self.passed, cost=self.cost, truth_class=self.truth_class
        )


def escalation(cheap: Attempt) -> list[str]:
    if not cheap.answered:
        return [NO_ANSWER]
    return [f for f in cheap.flags if f in ESCALATE]


def climb(
    case: str,
    rep: int,
    truth_class: TruthClass | None,
    rules: Attempt,
    cheap: Attempt,
    strong: Attempt,
) -> Step:
    if rules.answered:
        rung, passed, cost, answered, triggers = "rules", rules.passed, rules.cost, True, []
    elif triggers := escalation(cheap):
        rung, passed, answered = "strong", strong.passed, strong.answered
        cost = round(cheap.cost + strong.cost, 6)
    else:
        rung, passed, cost, answered = "cheap", cheap.passed, cheap.cost, True
    return Step(case=case, rep=rep, truth_class=truth_class, rung=rung, passed=passed,
                cost=cost, answered=answered, triggers=triggers)  # fmt: skip


class Arm(BaseModel):
    """One column of the replay: a rung alone, or the ladder."""

    name: str
    runs: int
    answered: int
    passes: int
    change: float | None
    not_change: float | None
    balanced: float | None
    pooled: float
    spent: float
    cost_per_solved: float | None


def arm(name: str, results: Sequence[tuple[str, TruthClass | None, Attempt]]) -> Arm:
    rate_list = class_rates((c, k, a.passed) for c, k, a in results)
    rates = {r.truth_class: r.pass_rate for r in rate_list}
    passes = sum(a.passed for _, _, a in results)
    spent = sum(a.cost for _, _, a in results)
    return Arm(
        name=name, runs=len(results), answered=sum(a.answered for _, _, a in results),
        passes=passes, change=rates.get("change"), not_change=rates.get("not_change"),
        balanced=balanced_rate(rate_list), pooled=passes / len(results) if results else 0.0,
        spent=spent, cost_per_solved=spent / passes if passes else None,
    )  # fmt: skip


class Replay(BaseModel):
    cheap: str
    strong: str
    arms: list[Arm]
    steps: list[Step]
    skipped: list[str]  # case:rep left out, and why

    def markdown(self) -> str:
        def pct(x: float | None) -> str:
            return f"{x:.0%}" if x is not None else "n/a"

        lines = [
            f"### Ladder: rules, then {self.cheap}, then {self.strong} on lint flags",
            "",
            "| arm | runs | answered | change | not_change | balanced | pooled | spent "
            "| cost per solved |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for a in self.arms:
            per = f"${a.cost_per_solved:.4f}" if a.cost_per_solved is not None else "n/a"
            lines.append(
                f"| {a.name} | {a.runs} | {a.answered} | {pct(a.change)} | {pct(a.not_change)} "
                f"| {pct(a.balanced)} | {pct(a.pooled)} | ${a.spent:.4f} | {per} |"
            )
        by_rung = {r: [s for s in self.steps if s.rung == r] for r in ("rules", "cheap", "strong")}
        lines += [
            "",
            f"Ladder rungs: rules answered {len(by_rung['rules'])}, {self.cheap} kept "
            f"{len(by_rung['cheap'])}, escalated to {self.strong} {len(by_rung['strong'])}. "
            f"Escalation lints: {', '.join(sorted(ESCALATE))}, plus a cheap run with no answer.",
            "Answered counts answers given (a rules abstention or a model run with no answer "
            "is not one). Cost per solved is spend over passes.",
        ]
        if self.skipped:
            lines += ["", f"Left out ({len(self.skipped)}): " + "; ".join(self.skipped)]
        return "\n".join(lines) + "\n"


def replay(
    cheap: str,
    strong: str,
    cases: dict[str, TruthClass | None],
    rules: dict[str, Attempt],
    cheap_runs: dict[tuple[str, int], Attempt],
    strong_runs: dict[tuple[str, int], Attempt],
    repeats: int,
) -> Replay:
    rows: dict[str, list[tuple[str, TruthClass | None, Attempt]]] = {
        "rules": [],
        cheap: [],
        strong: [],
    }
    steps: list[Step] = []
    skipped: list[str] = []
    for case in sorted(cases):
        for rep in range(repeats):
            c, s = cheap_runs.get((case, rep)), strong_runs.get((case, rep))
            if case not in rules or c is None or s is None:
                missing = [n for n, a in (("cheap", c), ("strong", s)) if a is None]
                skipped.append(f"{case}:{rep} no scored {' or '.join(missing) or 'rules'} run")
                continue
            k = cases[case]
            rows["rules"].append((case, k, rules[case]))
            rows[cheap].append((case, k, c))
            rows[strong].append((case, k, s))
            steps.append(climb(case, rep, k, rules[case], c, s))
    ladder = [
        (s.case, s.truth_class, Attempt(passed=s.passed, cost=s.cost, answered=s.answered))
        for s in steps
    ]
    arms = [arm(n, r) for n, r in rows.items()] + [arm("ladder", ladder)]
    return Replay(cheap=cheap, strong=strong, arms=arms, steps=steps, skipped=skipped)
