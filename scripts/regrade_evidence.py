"""A second, post-hoc grade for evidence precision, from the committed run logs.

    uv run python scripts/regrade_evidence.py > results/evidence-precision-posthoc.md

The main grade asks that every cited id exists in the scenario's telemetry and that at
least one is in the truth's `required_evidence_any`. It does not penalise citing more: an
answer with the right labels that cites every id in the store passes every scenario.

This script reads each logged answer from `runs.sqlite`, regrades it with today's grader
(single-service, as those sweeps were graded) and then asks how many passing runs would
still pass if a stated share of the cited ids had to be required ones. `required_evidence_any`
was written to be hit once, not to list every id a careful answer could cite, so this
precision is a lower bound on how focused an answer was, and the floors are arbitrary.
Nothing here changes the main grade or any published number.
"""

import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from rootcause.adapters.fixtures import discover, load_case
from rootcause.application.agent import RunResult
from rootcause.application.evals import grade
from rootcause.application.executor import ToolCall
from rootcause.domain.answers import Diagnosis, Outcome
from rootcause.domain.scenario import Case

ROOT = Path(__file__).resolve().parent.parent
SWEEPS = ("models-and-retrieval-2026-10-15", "loop-2026-10-15", "langgraph-2026-10-15")
FLOORS = (0.10, 0.25, 0.50)


def cite_everything(case: Case) -> bool:
    """Does the main grade pass the truth's labels with every id in the store cited?"""
    truth = case.scenario.truth
    assert truth is not None
    answer = Diagnosis(
        category=truth.category, service=truth.service, summary="x",
        evidence=sorted(case.store.evidence_ids()), recommended_action="x", confidence=1.0,
    )  # fmt: skip
    looked = [ToolCall(step=1, tool_use_id="t", name="query_logs", args={}, gated=False)]
    result = RunResult[Diagnosis](
        run_id="r", agent="a", outcome=Outcome.ANSWERED, answer=answer, steps=1,
        tool_calls=looked, cost_usd=0.0, wall_ms=0.0,
    )  # fmt: skip
    return grade(case, result, provider="p", mode="single", tool_calls=1).passed


def tool_calls(db: sqlite3.Connection, run_id: str) -> int:
    (own,) = db.execute(
        "select count(*) from events where run_id = ? and kind = 'tool_call'", (run_id,)
    ).fetchone()
    children = db.execute("select run_id from runs where parent_run_id = ?", (run_id,)).fetchall()
    return own + sum(tool_calls(db, child) for (child,) in children)


def logged_runs(sweep: str) -> list[tuple[str, bool, float, int]]:
    """(provider and agent, passed the main grade, share of cited ids that are required,
    ids cited) for every top-level run that ended with an answer."""
    db = sqlite3.connect(
        f"file:{ROOT / 'results' / 'sweeps' / sweep / 'runs.sqlite'}?mode=ro", uri=True
    )
    cases: dict[str, Case] = {}
    out = []
    for run_id, scenario_id, provider in db.execute(
        "select run_id, scenario_id, provider from runs where parent_run_id is null"
    ):
        payloads = db.execute(
            "select payload from events where run_id = ? and kind = 'outcome' order by seq",
            (run_id,),
        ).fetchall()
        if not payloads:
            continue  # killed before it finished; the sweep ran it again
        result = RunResult[Diagnosis].model_validate(json.loads(payloads[-1][0]))
        if result.answer is None:
            continue
        case = cases.setdefault(scenario_id, load_case(ROOT / "scenarios" / scenario_id))
        truth = case.scenario.truth
        assert truth is not None
        mode = "multi" if result.agent == "orchestrator" else "single"
        graded = grade(
            case, result, provider=provider, mode=mode,
            tool_calls=tool_calls(db, run_id), strict_service=True,
        )  # fmt: skip
        cited = result.answer.evidence
        required = sum(e in truth.required_evidence_any for e in cited)
        out.append((f"{provider}, {mode}", graded.passed, required / len(cited), len(cited)))
    return out


def main() -> None:
    cases = [
        load_case(p) for root in ("scenarios", "scenarios-external") for p in discover(ROOT / root)
    ]
    everything = sum(cite_everything(c) for c in cases)
    stores = [len(c.store.evidence_ids()) for c in cases]
    print("# Evidence precision: a post-hoc second grade\n")
    print(
        "Written by `scripts/regrade_evidence.py` from the committed `runs.sqlite` files, after "
        "the results were public. The main grade and every published pass rate are unchanged. "
        "The floors below were not chosen before the runs and no truth lists decoy ids to "
        "forbid, so read this as a description of the grader, not a result about the models.\n"
    )
    print(
        f"**The main grade does not penalise over-citation.** Right category and service plus "
        f"every id in the scenario's telemetry passes {everything} of {len(cases)} scenarios "
        f"(median {statistics.median(stores):g} ids per scenario).\n"
    )
    print(
        "Precision is the share of an answer's cited ids that are in the truth's "
        '`required_evidence_any`. "Still pass" counts runs that pass the main grade and meet '
        "the floor.\n"
    )
    head = ["sweep", "provider, mode", "answers", "pass, main grade", "ids cited, median"]
    head += ["precision, median", *(f"still pass at {f:.0%}" for f in FLOORS)]
    print("| " + " | ".join(head) + " |")
    print("|---" * (6 + len(FLOORS)) + "|")
    for sweep in SWEEPS:
        groups: dict[str, list[tuple[bool, float, int]]] = defaultdict(list)
        for key, passed, precision, cited in logged_runs(sweep):
            groups[key].append((passed, precision, cited))
            groups["all"].append((passed, precision, cited))
        for key in sorted(groups, key=lambda k: (k == "all", k)):
            runs = groups[key]
            passing = [r for r in runs if r[0]]
            cells = "".join(f" {sum(p >= f for _, p, _ in passing)} |" for f in FLOORS)
            print(
                f"| {sweep} | {key} | {len(runs)} | {len(passing)} "
                f"| {statistics.median(c for _, _, c in runs):g} "
                f"| {statistics.median(p for _, p, _ in runs):.2f} |" + cells
            )
    print(
        "\nThe models sweep log holds 527 answers: the 525 graded rows and the two runs listed "
        "in its `EXCLUDED.md`, which fail the main grade. Its three Opus high-effort "
        "configurations (no search, BM25, hybrid) share one provider name in the log."
    )


if __name__ == "__main__":
    sys.exit(main())
