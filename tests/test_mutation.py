"""Mutation testing of the investigator brief: verdicts follow litmus's rule that a mutant
is killed only by a trusted run, and an untrusted one is never counted as a kill."""

from test_sweep import grade

from rootcause.application.mutation import verdicts
from rootcause.application.sweep import Row
from rootcause.domain.answers import Outcome


def row(config: str, sid: str, ok: bool, rep: int = 0) -> Row:
    return Row(config=config, scenario_id=sid, rep=rep, grade=grade(sid, ok))


def test_killed_survived_and_inconclusive():
    baseline = [row("base", s, True, r) for s in ("a", "b") for r in range(3)]
    baseline += [row("base", "c", ok, r) for r, ok in enumerate([True, False, True])]
    mutants = [
        row("m1", "a", False), row("m1", "b", True), row("m1", "c", False),  # killed on a
        row("m2", "a", True), row("m2", "b", True), row("m2", "c", False),  # c is not stable
    ]  # fmt: skip
    errored = row("m3", "a", False)
    errored.grade.outcome = Outcome.ERROR
    result = verdicts(baseline, mutants, [errored], names=["m1", "m2", "m3"])
    assert result["m1"].verdict == "killed" and result["m1"].killed_by == ["a"]
    assert result["m2"].verdict == "survived"
    assert result["m3"].verdict == "inconclusive"
