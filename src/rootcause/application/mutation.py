"""Verdicts for mutants of the investigator brief, after litmus's rules.

A mutant is a copy of the brief with one deliberate defect (an instruction
deleted, a rule inverted, a fact made wrong). It is killed when it fails a
scenario that the unmodified brief passed in every baseline run: that is the
evidence the suite can tell the defect apart from the original. Scenarios the
baseline does not pass every time can't show a kill, because a failure there
is indistinguishable from ordinary variance. A mutant with no kill but with
runs that ended in harness errors is inconclusive, never survived.
"""

from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

from rootcause.application.sweep import Row

Verdict = Literal["killed", "survived", "inconclusive", "not run"]


class MutantResult(BaseModel):
    name: str
    verdict: Verdict
    killed_by: list[str]
    runs: int
    errors: int


def stable_scenarios(baseline: Sequence[Row]) -> set[str]:
    by_scenario: dict[str, list[bool]] = defaultdict(list)
    for r in baseline:
        by_scenario[r.scenario_id].append(r.grade.passed)
    return {sid for sid, results in by_scenario.items() if results and all(results)}


def verdicts(
    baseline: Sequence[Row], mutants: Sequence[Row], errors: Sequence[Row], names: Sequence[str]
) -> dict[str, MutantResult]:
    stable = stable_scenarios(baseline)
    out = {}
    for name in names:
        rows = [r for r in mutants if r.config == name and r.grade.status == "scored"]
        errs = [r for r in [*errors, *mutants] if r.config == name and r.grade.status == "error"]
        killed_by = sorted(
            {r.scenario_id for r in rows if r.scenario_id in stable and not r.grade.passed}
        )
        if killed_by:
            verdict: Verdict = "killed"
        elif errs:
            verdict = "inconclusive"
        elif rows:
            verdict = "survived"
        else:
            verdict = "not run"
        out[name] = MutantResult(
            name=name, verdict=verdict, killed_by=killed_by, runs=len(rows), errors=len(errs)
        )
    return out
