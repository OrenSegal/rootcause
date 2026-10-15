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

from rootcause.domain.scenario import Alert, Case, Scenario
from rootcause.domain.store import TelemetryStore
from rootcause.domain.telemetry import Deploy, LogLevel, LogLine, Span, SpanStatus

RUN_FIELDS = (
    "databaseId,name,displayTitle,headBranch,headSha,event,conclusion,createdAt,updatedAt,jobs"
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

    def fetch(self, repo: str, run_id: int) -> RawRun:
        run = json.loads(
            self.gh(["run", "view", str(run_id), "--repo", repo, "--json", RUN_FIELDS])
        )
        run["repository"] = repo
        failed_log = self.gh(["run", "view", str(run_id), "--repo", repo, "--log-failed"])
        return RawRun(run=run, failed_log=failed_log, commits=self._commits(repo, run))

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
            listed = [
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
                "files": json.loads(
                    self.gh(
                        [
                            "api",
                            f"repos/{repo}/commits/{c['oid']}",
                            "--jq",
                            "[.files[] | {status, filename}]",
                        ]
                    )
                ),
            }
            for c in listed
        ]


def _ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _logs(failed_log: str) -> list[LogLine]:
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
        out.append(
            LogLine(
                id=f"log-{len(out) + 1:04d}",
                ts=_ts(f"{match['ts']}.{frac}Z"),
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
        logs=_logs(raw.failed_log),
        metrics=[],
        spans=_spans(run),
        deploys=_deploys(run, raw.commits),
    )
    return Case(scenario=scenario, store=store)
