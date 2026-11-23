"""A failed GitHub Actions run, as investigation telemetry.

The mapping keeps the agent unchanged: the same tools that investigate a
production incident investigate a broken build.

    job log lines         -> logs (service = job slug)
    run / jobs / steps    -> one trace with nested spans
    commits under test    -> deploys (service = repository)
    workflow/config files -> config changes on that deploy

Fetching uses the `gh` CLI with read-only calls only. Nothing is posted.
"""

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rootcause.application.triage import DOC_JOB, ENVIRONMENT, GATE_JOB, is_doc
from rootcause.application.triage import matrix_stem as _matrix_stem
from rootcause.domain.scenario import Alert, Case, Scenario
from rootcause.domain.store import TelemetryStore
from rootcause.domain.telemetry import Deploy, LogLevel, LogLine, Span, SpanStatus

RUN_FIELDS = (
    "databaseId,name,displayTitle,headBranch,headSha,event,conclusion,createdAt,updatedAt,jobs,"
    "attempt"
)
CONFIG_NAMES = {"Dockerfile", "Makefile", "package.json", "pyproject.toml", "uv.lock", "setup.cfg"}
CONFIG_SUFFIXES = (".yml", ".yaml", ".toml", ".ini", ".cfg", ".lock")
ANSI = re.compile(r"(?:\x1b|\^\[)\[[0-9;]*m")
LINE = re.compile(r"^(?P<ts>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(?P<frac>\d+))?Z ?(?P<text>.*)$")

Runner = Callable[[list[str]], str]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def is_config(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith(".github/") or name in CONFIG_NAMES or name.endswith(CONFIG_SUFFIXES)


@dataclass(frozen=True)
class RawRun:
    """Exactly what `gh` returned. Stored with recorded scenarios as their source."""

    run: dict[str, Any]
    failed_log: str
    commits: list[dict[str, Any]]

    @classmethod
    def load(cls, path: Path) -> RawRun:
        return cls(
            run=json.loads((path / "run.json").read_text()),
            failed_log=(path / "failed.log").read_text(),
            commits=json.loads((path / "commits.json").read_text()),
        )

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "run.json").write_text(json.dumps(self.run, indent=2))
        (path / "failed.log").write_text(self.failed_log)
        (path / "commits.json").write_text(json.dumps(self.commits, indent=2))


def _gh(args: list[str]) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


class GhCli:
    def __init__(self, runner: Runner = _gh) -> None:
        self.gh = runner

    def fetch(
        self, repo: str, run_id: int, *, base_sha: str | None = None, attempt: int | None = None
    ) -> RawRun:
        """`base_sha` is the last green commit: the commits under test are then everything
        after it, from the compare API. `attempt` reads an earlier attempt of a rerun run."""
        view = ["run", "view", str(run_id), "--repo", repo]
        if attempt is not None:
            view += ["--attempt", str(attempt)]
        run = json.loads(self.gh([*view, "--json", RUN_FIELDS]))
        run["repository"] = repo
        failed_log = self.gh([*view, "--log-failed"])
        commits = (
            self._compare(repo, base_sha, run["headSha"]) if base_sha else self._commits(repo, run)
        )
        return RawRun(run=run, failed_log=failed_log, commits=commits)

    def fix_files(self, repo: str, failed_sha: str, fixed_sha: str) -> dict[str, Any]:
        """What changed between the failed commit and the next green one: the fix."""
        out = json.loads(
            self.gh(
                [
                    "api",
                    f"repos/{repo}/compare/{failed_sha}...{fixed_sha}",
                    "--jq",
                    "{status, commits: [.commits[].sha], files: [.files[].filename], "
                    "merges: [.commits[] | select((.parents | length) > 1) | .sha]}",
                ]
            )
        )
        # "diverged" means the branch was force-pushed or rebased: the diff is not a fix.
        return out | {"files": sorted(set(out["files"]))}

    def _compare(self, repo: str, base: str, head: str) -> list[dict[str, Any]]:
        """Commits after `base` up to `head`. `base` is a branch name (a PR's first run:
        everything since the merge base) or the last green sha, which must be an ancestor."""
        out = json.loads(
            self.gh(
                [
                    "api",
                    f"repos/{repo}/compare/{base}...{head}",
                    "--jq",
                    "{status, commits: [.commits[] | {oid: .sha, committedDate: "
                    '.commit.committer.date, messageHeadline: (.commit.message | split("\\n")[0]), '
                    'author: (.author.login // "")}]}',
                ]
            )
        )
        if re.fullmatch(r"[0-9a-f]{40}", base) and out["status"] not in ("ahead", "identical"):
            raise ValueError(f"history {out['status']} since the last green run (force-push)")
        return [c | {"files": self._files(repo, c["oid"])} for c in out["commits"]]

    def _files(self, repo: str, sha: str) -> list[dict[str, str]]:
        return json.loads(
            self.gh(
                ["api", f"repos/{repo}/commits/{sha}", "--jq", "[.files[] | {status, filename}]"]
            )
        )

    def _commits(self, repo: str, run: dict[str, Any]) -> list[dict[str, Any]]:
        if run["event"] == "pull_request":
            prs = json.loads(
                self.gh(
                    [
                        "pr",
                        "list",
                        "--repo",
                        repo,
                        "--head",
                        run["headBranch"],
                        "--state",
                        "all",
                        "--json",
                        "number",
                    ]
                )
            )
            listed = json.loads(
                self.gh(["pr", "view", str(prs[0]["number"]), "--repo", repo, "--json", "commits"])
            )["commits"]
        else:
            listed: list[dict[str, Any]] = [
                {
                    "oid": run["headSha"],
                    "committedDate": run["createdAt"],
                    "messageHeadline": run["displayTitle"],
                    "authors": [],
                }
            ]
        return [
            {
                "oid": c["oid"],
                "committedDate": c["committedDate"],
                "messageHeadline": c["messageHeadline"],
                "author": c["authors"][0]["login"] if c.get("authors") else "",
                "files": self._files(repo, c["oid"]),
            }
            for c in listed
        ]


def _ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _step_at(run: dict[str, Any], job: str, ts: datetime) -> str | None:
    """`gh run view --log-failed` can print UNKNOWN STEP for every line; the run's step
    timings (whole seconds) say which step was running."""
    for j in run.get("jobs", []):
        if j["name"] != job:
            continue
        for step in j["steps"]:
            start, end = step.get("startedAt"), step.get("completedAt")
            if start and end and _ts(start) <= ts.replace(microsecond=0) <= _ts(end):
                return step["name"]
    return None


def _logs(failed_log: str, run: dict[str, Any] | None = None) -> list[LogLine]:
    out: list[LogLine] = []
    for line in failed_log.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        job, step, rest = parts
        match = LINE.match(rest.lstrip("\ufeff"))
        if not match:
            continue
        text = ANSI.sub("", match["text"]).strip()
        if not text or text == "##[endgroup]":
            continue
        level: LogLevel = "info"
        if text.startswith("##[error]") or "(error)" in text:
            level = "error"
        elif text.startswith("##[warning]") or "(warning)" in text:
            level = "warn"
        text = re.sub(r"^##\[(error|warning|group)\]", "", text)
        frac = (match["frac"] or "0")[:6].ljust(6, "0")
        ts = _ts(f"{match['ts']}.{frac}Z")
        if step == "UNKNOWN STEP" and run is not None:
            step = _step_at(run, job, ts) or step
        out.append(
            LogLine(
                id=f"log-{len(out) + 1:04d}",
                ts=ts,
                service=slug(job),
                level=level,
                message=f"{step}: {text}",
            )
        )
    return out


def _deploys(run: dict[str, Any], commits: list[dict[str, Any]]) -> list[Deploy]:
    shas = [c["oid"] for c in commits]
    under_test = commits[: shas.index(run["headSha"]) + 1] if run["headSha"] in shas else commits
    repo = run.get("repository", "repo").rsplit("/", 1)[-1]
    deploys = []
    for c in under_test:
        short = c["oid"][:10]
        before, after = {}, {}
        for f in c["files"]:
            if not is_config(f["filename"]):
                continue
            if f["status"] != "added":
                before[f["filename"]] = "previous"
            if f["status"] != "removed":
                after[f["filename"]] = f"{f['status']} in {short}"
        deploys.append(
            Deploy(
                id=short,
                service=repo,
                version=short,
                ts=_ts(c["committedDate"]),
                author=c["author"],
                summary=c["messageHeadline"],
                config_before=before,
                config_after=after,
                files=[f["filename"] for f in c["files"]],
            )
        )
    return deploys


def _spans(run: dict[str, Any]) -> list[Span]:
    trace = f"run-{run['databaseId']}"

    def status(conclusion: str | None) -> SpanStatus:
        return "error" if conclusion == "failure" else "ok"

    def duration(start: str, end: str) -> float:
        return (_ts(end) - _ts(start)).total_seconds() * 1000

    spans = [
        Span(
            span_id=trace,
            trace_id=trace,
            service=slug(run["name"]),
            name=run["name"],
            start=_ts(run["createdAt"]),
            duration_ms=duration(run["createdAt"], run["updatedAt"]),
            status=status(run["conclusion"]),
        )
    ]
    for job in run["jobs"]:
        job_span = f"job-{job['databaseId']}"
        spans.append(
            Span(
                span_id=job_span,
                trace_id=trace,
                parent_id=trace,
                service=slug(job["name"]),
                name=job["name"],
                start=_ts(job["startedAt"]),
                duration_ms=duration(job["startedAt"], job["completedAt"]),
                status=status(job["conclusion"]),
            )
        )
        for step in job["steps"]:
            if step["conclusion"] not in ("success", "failure"):
                continue
            spans.append(
                Span(
                    span_id=f"{job_span}-step-{step['number']}",
                    trace_id=trace,
                    parent_id=job_span,
                    service=slug(job["name"]),
                    name=step["name"],
                    start=_ts(step["startedAt"]),
                    duration_ms=duration(step["startedAt"], step["completedAt"]),
                    status=status(step["conclusion"]),
                )
            )
    return spans


def case_from_raw(raw: RawRun) -> Case:
    run = raw.run
    failed = sorted(
        (j for j in run["jobs"] if j["conclusion"] == "failure"), key=lambda j: j["startedAt"]
    )
    job = failed[0] if failed else run["jobs"][0]
    step = next((s["name"] for s in job["steps"] if s["conclusion"] == "failure"), "unknown")
    alert = Alert(
        severity="ci",
        service=slug(job["name"]),
        summary=f"workflow {run['name']} failed on {run['headBranch']} at "
        f"{run['headSha'][:10]}: step '{step}' in job '{job['name']}' failed.",
    )
    scenario = Scenario(
        id=f"ci-{run['databaseId']}",
        title=run["displayTitle"],
        alert=alert,
        provenance="recorded",
    )
    store = TelemetryStore(
        logs=_logs(raw.failed_log, run),
        metrics=[],
        spans=_spans(run),
        deploys=_deploys(run, raw.commits),
    )
    return Case(scenario=scenario, store=store)


# --- Mining labelled cases from red-to-green pairs (scripts/mine_ci.py, docs/ci-benchmark-scope.md)

PIN_NAMES = {
    "uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", "Cargo.lock", "go.sum",
}  # fmt: skip
PIN_PREFIXES = ("requirements", "constraints")


def is_pin(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    in_pin_file = any(part.startswith(PIN_PREFIXES) for part in path.split("/"))
    return name in PIN_NAMES or (in_pin_file and name.endswith(".txt"))


@dataclass(frozen=True)
class Candidate:
    """A break on one workflow and branch: the last green run before it, the first red run,
    and the next green run. `rerun` means the red run's own later attempt went green."""

    green: dict[str, Any] | None
    red: dict[str, Any]
    fixed: dict[str, Any]
    rerun: bool = False

    @property
    def flaky(self) -> bool:
        return self.rerun or self.red["head_sha"] == self.fixed["head_sha"]


def candidates(runs: list[dict[str, Any]]) -> list[Candidate]:
    """`runs` as the REST API lists them (completed runs, latest attempt each), grouped by
    workflow and branch, so each pull request's branch is its own timeline. Only the first
    red run of a streak is a case: later reds share its cause."""
    out: list[Candidate] = []
    by_workflow: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for run in runs:
        if run["conclusion"] in ("success", "failure"):
            key = (run["workflow_id"], run.get("head_repository"), run.get("head_branch"))
            by_workflow.setdefault(key, []).append(run)
    for wf_runs in by_workflow.values():
        wf_runs.sort(key=lambda r: r["created_at"])
        last_green: dict[str, Any] | None = None
        red: dict[str, Any] | None = None
        for run in wf_runs:
            if run["conclusion"] == "failure":
                red = red or run
                continue
            if red is not None:
                out.append(Candidate(green=last_green, red=red, fixed=run))
                red = None
            elif run.get("run_attempt", 1) > 1:
                # Its first attempt failed and a rerun of the same commit passed.
                out.append(Candidate(green=last_green, red=run, fixed=run, rerun=True))
            last_green = run
    return out


@dataclass(frozen=True)
class Label:
    category: str
    guilty: str | None  # full sha of the commit under test that broke the build
    reason: str


NOTE_PREFIXES = (
    "changes",
    "changelog",
    "history",
    "news",
    "authors",
    "release-notes",
    "contributors",
)


def is_note(path: str) -> bool:
    """Changelogs and author lists: nearly every commit edits them, so sharing one proves
    nothing about which commit a fix repairs."""
    return any(part.lower().startswith(NOTE_PREFIXES) for part in path.split("/"))


# pytest-xdist lost a worker: a segfault in the code under test or a starved runner.
WORKER_CRASH = re.compile(r"worker '[^']+' crashed while running")


# Jobs that test against an unreleased upstream ("Docutils HEAD", "Aiohttp tests (master)",
# "3.15-dev"): they break when upstream changes, with no commit under test to blame.
UPSTREAM_JOB = re.compile(
    r"\b(?:HEAD|master|nightly|dev|devel|pre-?release|upstream)\b|\d\.\d+-dev|\b\d\.\d+(?:a|b|rc)\d",
    re.IGNORECASE,
)


def _only_upstream_jobs_failed(jobs: list[dict[str, Any]]) -> bool:
    failed = [
        j["name"] for j in jobs if j["conclusion"] == "failure" and not GATE_JOB.search(j["name"])
    ]
    return bool(failed) and all(UPSTREAM_JOB.search(n) for n in failed)


def _matrix_sibling_passes(jobs: list[dict[str, Any]]) -> int:
    """With exactly one failed job (gate jobs aside), how many jobs of its matrix passed."""
    failed = [j for j in jobs if j["conclusion"] == "failure" and not GATE_JOB.search(j["name"])]
    if len(failed) != 1:
        return 0
    stem = _matrix_stem(failed[0]["name"])
    return sum(
        1
        for j in jobs
        if j["conclusion"] == "success" and j["name"] != failed[0]["name"]
        and _matrix_stem(j["name"]) == stem
    )  # fmt: skip


def _environment_error(raw: RawRun) -> str | None:
    """An environment error that every failed job shows. One job failing setup while
    another fails a real test is not an environment failure."""
    failed = [
        j["name"] for j in raw.run.get("jobs", [])
        if j["conclusion"] == "failure" and not GATE_JOB.search(j["name"])
    ]  # fmt: skip
    if not failed:
        found = ENVIRONMENT.search(ANSI.sub("", raw.failed_log))
        return found[0] if found else None
    by_job: dict[str, list[str]] = {}
    for line in ANSI.sub("", raw.failed_log).splitlines():
        by_job.setdefault(line.split("\t", 1)[0], []).append(line)
    hits = [ENVIRONMENT.search("\n".join(by_job.get(name, []))) for name in failed]
    first = hits[0]
    return first[0] if first is not None and all(hits) else None


def _workflow_commit(raw: RawRun, fix: list[str]) -> str | None:
    """The one tested commit that changed a workflow file the fix also changed."""
    fixed = {f for f in fix if f.startswith(".github/")}
    hits = [
        c["oid"] for c in raw.commits if fixed & {f["filename"] for f in c["files"]}
    ]  # fmt: skip
    return hits[0] if len(hits) == 1 else None


def label(
    candidate: Candidate, raw: RawRun, fix: list[str], *, fix_merged: bool = False
) -> Label | str:
    """The rules of docs/ci-benchmark-scope.md, first match wins. A string drops the case
    and says why, so drop rates can be reported per reason."""
    if candidate.flaky:
        if candidate.red.get("event") == "pull_request":
            # A pull-request run tests a merge with the base branch, so a later run of the
            # same head can pass because the base was fixed, not because the failure was flaky.
            return "a pull-request rerun passed, but it tested a new merge with the base"
        return Label("infrastructure", None, "a rerun of the same commit passed")
    env = _environment_error(raw)
    if env:
        # A tested commit that edited the workflow the fix edits back caused the "environment"
        # error (asking for a Python the runner lacks, say).
        guilty = _workflow_commit(raw, fix)
        if guilty:
            return Label(
                "config_change",
                guilty,
                f"a tested workflow edit caused {env!r}; the fix edits it",
            )
        return Label("infrastructure", None, f"the failed log shows an environment error: {env!r}")
    if _only_upstream_jobs_failed(raw.run.get("jobs", [])):
        return "only jobs testing an unreleased upstream failed"
    if WORKER_CRASH.search(raw.failed_log):
        return "test workers crashed (a segfault or the runner; the rules cannot tell)"
    siblings = _matrix_sibling_passes(raw.run.get("jobs", []))
    if siblings >= 2:
        return f"one matrix job failed while {siblings} siblings passed (flaky or platform bug)"
    if fix_merged:
        # Merging the base brings in everything that landed there, so the diff is not the fix.
        return "the fix merged another branch, so its diff is not only the fix"
    fix_set = {f for f in fix if not is_note(f)}
    if not fix_set:
        return "the fix changed only changelogs or nothing"
    touched = {
        c["oid"]: {f["filename"] for f in c["files"] if not is_note(f["filename"])}
        for c in raw.commits
    }
    hits = [sha for sha, files in touched.items() if files & fix_set]
    if all(is_pin(f) for f in fix_set):
        if hits:
            return "a tested commit changed the pins the fix changed"
        return Label(
            "dependency_failure", None, "the fix only changed pins no tested commit touched"
        )
    if len(hits) != 1:
        return f"{len(hits)} tested commits touched the files the fix changed"
    shared = touched[hits[0]] & fix_set
    # The workflow name counts too: "Build docs" runs a job called just "build".
    failed_jobs = [raw.run.get("name", "")] + [
        j["name"] for j in raw.run.get("jobs", []) if j["conclusion"] == "failure"
    ]
    if all(is_doc(f) for f in shared) and not any(DOC_JOB.search(n) for n in failed_jobs):
        # A docs edit does not break a test job; the overlap is a coincidence.
        return "the fix shares only documentation with the tested commit, and no docs job failed"
    if all(is_config(f) for f in fix_set):
        return Label(
            "config_change", hits[0], "the fix only changed config one tested commit touched"
        )
    return Label(
        "bad_code_deploy", hits[0], "the fix changed files exactly one tested commit touched"
    )
