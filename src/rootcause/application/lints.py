"""Free checks on a model's CI answer that predict it is wrong.

Each lint is a pure function of the case's telemetry, the answer, the run's tool
calls and the rules' answer (None when the rules abstained), and returns True when
it flags the answer. A run with no answer is flagged by none of them; the ladder
escalates it on its own (see application/ladder.py).
"""

from collections import defaultdict
from collections.abc import Callable, Sequence

from pydantic import BaseModel

from rootcause.application.executor import ToolCall
from rootcause.application.triage import (
    failed_jobs,
    failed_steps,
    failing_lines,
    jobs,
    matrix_stem,
    names,
)
from rootcause.domain.answers import Diagnosis
from rootcause.domain.scenario import category_class
from rootcause.domain.store import TelemetryStore
from rootcause.domain.telemetry import Span

LOW_CONFIDENCE = 0.7


def cited_commits(store: TelemetryStore, answer: Diagnosis) -> list[str]:
    deploys = {d.id for d in store.deploys}
    return [e for e in answer.evidence if e in deploys]


def blamed_commit_unnamed(
    store: TelemetryStore, answer: Diagnosis, calls: Sequence[ToolCall], rule: Diagnosis | None
) -> bool:
    """A change answer whose cited commits change no file the failing log lines name."""
    if category_class(answer.category) != "change":
        return False
    lines = failing_lines(store.logs, store.spans)
    blamed = [d for d in store.deploys if d.id in cited_commits(store, answer)]
    return not any(names(line, f) for d in blamed for f in d.files for line in lines)


def change_without_commit(
    store: TelemetryStore, answer: Diagnosis, calls: Sequence[ToolCall], rule: Diagnosis | None
) -> bool:
    """A change answer that cites no commit under test."""
    return category_class(answer.category) == "change" and not cited_commits(store, answer)


def no_failed_job_log_query(
    store: TelemetryStore, answer: Diagnosis, calls: Sequence[ToolCall], rule: Diagnosis | None
) -> bool:
    """No query_logs call that could return a failed job's lines: one filtered to a
    failed job, or one with no job filter."""
    failed = {s.service for s in store.spans if s.parent_id and s.status == "error"}
    return not any(
        c.name == "query_logs" and c.args.get("service") in failed | {None} for c in calls
    )


def low_confidence(
    store: TelemetryStore, answer: Diagnosis, calls: Sequence[ToolCall], rule: Diagnosis | None
) -> bool:
    return answer.confidence < LOW_CONFIDENCE


def not_change_but_matrix_wide(
    store: TelemetryStore, answer: Diagnosis, calls: Sequence[ToolCall], rule: Diagnosis | None
) -> bool:
    """A not-change answer while one step fails in every job of a matrix of two or more."""
    if category_class(answer.category) != "not_change":
        return False
    by_stem: dict[str, list[Span]] = defaultdict(list)
    for job in jobs(store.spans):
        by_stem[matrix_stem(job.name)].append(job)
    failed = {j.span_id for j in failed_jobs(store.spans)}
    for matrix in by_stem.values():
        if len(matrix) < 2 or any(j.span_id not in failed for j in matrix):
            continue
        steps = [set(failed_steps(store.spans, j)) for j in matrix]
        if set.intersection(*steps):
            return True
    return False


def disagrees_with_rule(
    store: TelemetryStore, answer: Diagnosis, calls: Sequence[ToolCall], rule: Diagnosis | None
) -> bool:
    """The rules answered, and this answer differs in class or, for a change, in commit."""
    if rule is None:
        return False
    if category_class(answer.category) != category_class(rule.category):
        return True
    return cited_commits(store, rule) != [] and not set(cited_commits(store, rule)) & set(
        cited_commits(store, answer)
    )


Lint = Callable[[TelemetryStore, Diagnosis, Sequence[ToolCall], Diagnosis | None], bool]
LINTS: dict[str, Lint] = {
    "blamed_commit_unnamed": blamed_commit_unnamed,
    "change_without_commit": change_without_commit,
    "no_failed_job_log_query": no_failed_job_log_query,
    "low_confidence": low_confidence,
    "not_change_but_matrix_wide": not_change_but_matrix_wide,
    "disagrees_with_rule": disagrees_with_rule,
}


def flags(
    store: TelemetryStore,
    answer: Diagnosis | None,
    calls: Sequence[ToolCall],
    rule: Diagnosis | None,
) -> list[str]:
    """The names of the lints that flag this answer."""
    if answer is None:
        return []
    return [name for name, lint in LINTS.items() if lint(store, answer, calls, rule)]


class LintRow(BaseModel):
    model: str
    lint: str
    answers: int  # answers the lint was run on
    wrong: int  # of those, answers that did not pass
    flagged: int
    flagged_wrong: int

    @property
    def precision(self) -> float | None:
        return self.flagged_wrong / self.flagged if self.flagged else None

    @property
    def recall(self) -> float | None:
        return self.flagged_wrong / self.wrong if self.wrong else None


def lint_rows(model: str, answers: Sequence[tuple[bool, Sequence[str]]]) -> list[LintRow]:
    """Per lint, over (passed, flags) for each answered run of one model."""
    wrong = sum(not passed for passed, _ in answers)
    return [
        LintRow(
            model=model,
            lint=name,
            answers=len(answers),
            wrong=wrong,
            flagged=sum(name in f for _, f in answers),
            flagged_wrong=sum(name in f and not passed for passed, f in answers),
        )
        for name in LINTS
    ]


def lint_markdown(rows: Sequence[LintRow], title: str) -> str:
    def pct(x: float | None) -> str:
        return f"{x:.0%}" if x is not None else "n/a"

    lines = [
        f"#### {title}",
        "",
        "| model | lint | answers | wrong | flagged | flagged wrong | precision | recall |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {r.model} | {r.lint} | {r.answers} | {r.wrong} | {r.flagged} | {r.flagged_wrong} "
        f"| {pct(r.precision)} | {pct(r.recall)} |"
        for r in rows
    ]
    return "\n".join(lines) + "\n"
