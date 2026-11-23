"""Deterministic CI triage: answer only when a rule is sure, otherwise abstain.

The rules see what an investigator sees at triage time: the run's jobs and steps
(spans), the commits under test with their files (deploys) and the failed jobs' log
lines. They never see a fix, a re-run or a truth. First match wins:

- environment: every failed job's log shows a known environment error, and no commit
  under test edits a workflow file. Not caused by the change.
- docs_only: every commit under test changes only documentation, and a job that is
  not a docs or lint job failed. Not caused by the change.
- one_commit_named: the failing log lines name files that exactly one commit under
  test changed. Caused by that commit.
- matrix_minority: the failed jobs are under half of one matrix whose other jobs
  passed, and the failing log lines name no file a commit under test changed. Not
  caused by the change.

The patterns the miner labelled with (environment errors, gate jobs, matrix stems,
docs paths) live here so the triage rules and the miner share one definition.
"""

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from rootcause.domain.scenario import TruthClass
from rootcause.domain.telemetry import Deploy, LogLine, Span

# Error shapes that mean the environment failed, not the code under test.
ENVIRONMENT = re.compile(
    r"version '?[\w.]+'? with architecture '?\w+'? was not found|socket hang up"
    r"|Connection reset by peer|Could not resolve host|rate limit exceeded|API rate limit"
    r"|TLS handshake timeout|503 Service Unavailable|502 Bad Gateway"
    r"|runner has received a shutdown signal|lost communication with the server"
    r"|Failed to CreateArtifact|Unable to make request|Connection failed \[IP|Failed to fetch http"
    # Node's error codes, case-sensitive and whole: "ModuleNotFoundError" is not one.
    r"|(?-i:\bE(?:NOTFOUND|CONNRESET|CONNREFUSED|TIMEDOUT|AI_AGAIN)\b)",
    re.IGNORECASE,
)

# Jobs that only report whether the others passed ("check", "test-alls-green").
GATE_JOB = re.compile(
    r"^checks?$|alls?-green|^result\b|/ result$|status$|required checks", re.IGNORECASE
)
# More gate job names, used by the triage rules only (the miner keeps GATE_JOB).
MORE_GATES = re.compile(r"all tests passed|all checks pass", re.IGNORECASE)

DOC_JOB = re.compile(r"doc|sphinx|lint|pre-commit|style|spell|link", re.IGNORECASE)

# A log line that reports a failure: a pytest outcome or location, an exception, a
# type checker or linter error, or a formatter rewriting a file.
FAILING = re.compile(
    r"\bFAILED\b|\bERROR\b|\w+Error\b|\berror\b|^Fixing |would reformat|--> |File \""
    r"|:\d+: in |:\d+:\s*$"
)
# Lines that name a file without a failure in it: skips, passes and warnings.
NOT_FAILING = re.compile(r"SKIPPED|PASSED|XFAIL|XPASS|Warning|WARNING")


def matrix_stem(name: str) -> str:
    """The part of a job name a matrix shares: "test (3.13, windows)" -> "test",
    "Test windows-latest / pypy3.11" -> "Test", "ui:test-ui:windows-latest" -> "ui:test-ui"."""
    head = re.split(r"[\s(/]", name)[0]
    return head.rsplit(":", 1)[0] if ":" in head else head


def is_doc(path: str) -> bool:
    return path.startswith(("doc/", "docs/")) or path.endswith((".md", ".rst"))


def is_gate(name: str) -> bool:
    return bool(GATE_JOB.search(name) or MORE_GATES.search(name))


def root_span(spans: Sequence[Span]) -> Span | None:
    return next((s for s in spans if s.parent_id is None), None)


def jobs(spans: Sequence[Span]) -> list[Span]:
    """The run's jobs, gate jobs left out."""
    root = root_span(spans)
    if root is None:
        return []
    return [s for s in spans if s.parent_id == root.span_id and not is_gate(s.name)]


def failed_jobs(spans: Sequence[Span]) -> list[Span]:
    return [j for j in jobs(spans) if j.status == "error"]


def failed_steps(spans: Sequence[Span], job: Span) -> list[str]:
    return [s.name for s in spans if s.parent_id == job.span_id and s.status == "error"]


def _text(line: LogLine) -> str:
    """The message without its "step: " prefix, with Windows separators made POSIX."""
    return line.message.split(": ", 1)[-1].replace("\\", "/")


def failing_lines(logs: Sequence[LogLine], spans: Sequence[Span]) -> list[LogLine]:
    """Failure lines from the failed jobs' logs."""
    services = {j.service for j in failed_jobs(spans)}
    return [
        line
        for line in logs
        if line.service in services
        and FAILING.search(text := _text(line))
        and not NOT_FAILING.search(text)
    ]


def names(line: LogLine, path: str) -> bool:
    """The line names this repository path, whole: "tests/test_a.py" is not "test_a.py"."""
    return re.search(rf"(?<![\w.-]){re.escape(path)}(?!\w)", _text(line)) is not None


def named_commits(deploys: Sequence[Deploy], lines: Sequence[LogLine]) -> dict[str, list[LogLine]]:
    """Commit id -> failing lines that name a file it changed, for commits with any."""
    out: dict[str, list[LogLine]] = defaultdict(list)
    for d in deploys:
        for line in lines:
            if any(names(line, f) for f in d.files):
                out[d.id].append(line)
    return dict(out)


Spans, Deploys, Logs = Sequence[Span], Sequence[Deploy], Sequence[LogLine]


@dataclass(frozen=True)
class Verdict:
    rule: str
    truth_class: TruthClass
    commit: str | None  # the guilty commit, for a change verdict
    evidence: list[str]
    summary: str


def _environment(spans: Spans, deploys: Deploys, logs: Logs) -> Verdict | None:
    failed = failed_jobs(spans)
    if not failed or any(f.startswith(".github/") for d in deploys for f in d.files):
        return None
    by_job: dict[str, list[LogLine]] = defaultdict(list)
    for line in logs:
        by_job[line.service].append(line)
    hits = [
        next((x for x in by_job.get(j.service, []) if ENVIRONMENT.search(x.message)), None)
        for j in failed
    ]
    if not all(hits):
        return None
    found = [h for h in hits if h is not None]
    return Verdict(
        "environment", "not_change", None, [h.id for h in found[:5]],
        f"every failed job shows an environment error: {found[0].message[:200]}",
    )  # fmt: skip


def _docs_only(spans: Spans, deploys: Deploys, logs: Logs) -> Verdict | None:
    files = [f for d in deploys for f in d.files]
    failed = failed_jobs(spans)
    root = root_span(spans)
    named = [j.name for j in failed] + ([root.name] if root else [])
    if not files or not failed or not all(is_doc(f) for f in files):
        return None
    if any(DOC_JOB.search(n) for n in named):
        return None
    evidence = [line.id for line in failing_lines(logs, spans)[:3]] or [j.span_id for j in failed]
    return Verdict(
        "docs_only", "not_change", None, evidence,
        "the commits under test change only documentation, and a test job failed",
    )  # fmt: skip


def _one_commit_named(spans: Spans, deploys: Deploys, logs: Logs) -> Verdict | None:
    named = named_commits(deploys, failing_lines(logs, spans))
    if len(named) != 1:
        return None
    ((commit, lines),) = named.items()
    return Verdict(
        "one_commit_named", "change", commit, [commit, *(line.id for line in lines[:3])],
        f"the failing log lines name files only commit {commit} changed",
    )  # fmt: skip


def _matrix_minority(spans: Spans, deploys: Deploys, logs: Logs) -> Verdict | None:
    failed = failed_jobs(spans)
    stems = {matrix_stem(j.name) for j in failed}
    if len(stems) != 1:
        return None
    matrix = [j for j in jobs(spans) if matrix_stem(j.name) in stems]
    if len(failed) * 2 >= len(matrix):
        return None
    if named_commits(deploys, failing_lines(logs, spans)):
        return None
    lines = failing_lines(logs, spans)
    evidence = [line.id for line in lines[:3]] or [j.span_id for j in failed]
    return Verdict(
        "matrix_minority", "not_change", None, evidence,
        f"{len(failed)} of {len(matrix)} jobs of matrix {stems.pop()!r} failed, and the "
        "failing lines name no file a commit under test changed",
    )  # fmt: skip


RULES = (_environment, _docs_only, _one_commit_named, _matrix_minority)


def triage(
    spans: Sequence[Span], deploys: Sequence[Deploy], logs: Sequence[LogLine]
) -> Verdict | None:
    """The first rule that fires, or None to abstain."""
    for rule in RULES:
        if (verdict := rule(spans, deploys, logs)) is not None:
            return verdict
    return None


def rule_of(summary: str) -> str | None:
    """The rule named at the start of a rules answer's summary ("rule <name>: ...")."""
    match = re.match(r"rule (\w+):", summary)
    return match[1] if match else None
