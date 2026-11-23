"""Mine labelled CI-failure cases from public repositories (docs/ci-benchmark-scope.md).

For each repo: list completed pull-request runs (or push runs on the default branch) since
a date, find each break (first red run after a green one on the same branch, and the next
green run there), snapshot what `gh` returns, label it with the rules in
`adapters/github_actions.label`, and write a scenario.
Dropped breaks go to dropped.jsonl with the reason, so the drop rate can be reported.

    uv run python scripts/mine_ci.py pallets/flask pytest-dev/pytest --since 2026-08-25

`--rerun-only` mines only pull-request runs whose first attempt failed and a re-run of the
same run passed, labelled `infrastructure` with no commit and no other rule applied:

    uv run python scripts/mine_ci.py hynek/structlog --since 2026-08-25 --rerun-only \
        --out scenarios-ci/rerun --max-kept 6

Read-only `gh api` / `gh run view` calls only.
"""

import argparse
import json
import re
from pathlib import Path
from typing import Any

from rootcause.adapters.fixtures import write_case
from rootcause.adapters.github_actions import (
    GATE_JOB,
    Candidate,
    GhCli,
    Label,
    RawRun,
    candidates,
    case_from_raw,
    label,
)
from rootcause.domain.scenario import GroundTruth

# Jobs that check the pull request itself (its description, title, labels, changelog entry
# or author list). They fail until the author edits the PR and then pass on a re-run, so a
# passing re-run says nothing about flakiness (the "Check PR template" cases in the scope doc).
METADATA_JOB = re.compile(
    r"template|description|pr[- _]title|semantic|label|milestone|changelog|news|towncrier"
    r"|authors|mailmap|\bcla\b|\bdco\b",
    re.IGNORECASE,
)
# actions/checkout prints the merge commit a pull-request run tested, and the base it used.
MERGE_LINE = re.compile(
    r"HEAD is now at \w+ Merge (?P<head>[0-9a-f]{40}) into (?P<base>[0-9a-f]{40})"
)
# The benchmark leaves out cases whose log lines exceed this (scripts/freeze_ci_benchmark.py).
MAX_LOG_BYTES = 40_000_000


def list_runs(gh: GhCli, repo: str, event: str, branch: str, since: str) -> list[dict[str, Any]]:
    on_branch = f"&branch={branch}" if event == "push" else ""
    out = gh.gh(
        [
            "api",
            "--paginate",
            f"repos/{repo}/actions/runs?event={event}{on_branch}&status=completed"
            f"&created=>={since}&per_page=100",
            "--jq",
            ".workflow_runs[] | {id, name, event, workflow_id, conclusion, head_sha, head_branch, "
            "head_repository: .head_repository.full_name, created_at, run_attempt, html_url}",
        ]
    )
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def list_reruns(gh: GhCli, repo: str, since: str) -> list[dict[str, Any]]:
    """Successful pull-request runs on a later attempt. The API returns at most 1,000 runs
    per query, so a busy repository yields only its most recent ones.

    Most such runs are a fork's first attempt waiting for a maintainer's approval, which
    costs a call to rule out. So runs come in this order, each newest first: branches of the
    repository itself (never gated), then fork runs re-run by their own author, then the rest
    (often a maintainer approving, or re-running, a contributor's run)."""
    out = gh.gh(
        [
            "api",
            "--paginate",
            f"repos/{repo}/actions/runs?event=pull_request&status=success"
            f"&created=>={since}&per_page=100",
            "--jq",
            ".workflow_runs[] | select(.run_attempt > 1) | {id, name, event, workflow_id, "
            "conclusion, head_sha, head_branch, head_repository: .head_repository.full_name, "
            "created_at, run_attempt, html_url, actor: .actor.login, "
            "triggering_actor: .triggering_actor.login}",
        ]
    )
    runs = [json.loads(line) for line in out.splitlines() if line.strip()]
    runs.sort(key=lambda r: r["created_at"], reverse=True)

    def tier(run: dict[str, Any]) -> int:
        if run["head_repository"] == repo:
            return 0
        return 1 if run["actor"] == run["triggering_actor"] else 2

    return sorted(runs, key=tier)


def mine_one(
    gh: GhCli,
    repo: str,
    meta: dict[str, Any],
    c: Candidate,
    out: Path,
    max_commits: int,
    *,
    rerun_only: bool = False,
) -> str | None:
    """Writes the case and returns None, or returns why it was dropped."""
    if c.rerun:
        # The attempt's own conclusion is overwritten when failed jobs are re-run; its jobs
        # are not. An attempt awaiting approval has no jobs at all.
        failed_jobs = json.loads(
            gh.gh(
                [
                    "api",
                    f"repos/{repo}/actions/runs/{c.red['id']}/attempts/1/jobs?per_page=100",
                    "--jq",
                    '[.jobs[] | select(.conclusion == "failure") | .name]',
                ]
            )
        )
        if not failed_jobs:
            return "first attempt had no failed job (awaiting approval or cancelled)"
        real = [j for j in failed_jobs if not GATE_JOB.search(j)] or failed_jobs
        if rerun_only and all(METADATA_JOB.search(j) for j in real):
            return f"only pull-request metadata checks failed: {real[:3]}"
    if rerun_only:
        base = meta["branch"]  # every commit the pull request adds to the default branch
    elif c.green is not None:
        base = c.green["head_sha"]
    elif c.red.get("head_branch") != meta["branch"]:
        base = meta["branch"]  # a PR's first run: everything since it left the default branch
    elif c.flaky:
        base = None
    else:
        return "no green run before the break inside the window"
    raw = gh.fetch(repo, c.red["id"], base_sha=base, attempt=1 if c.rerun else None)
    if not raw.failed_log.strip():
        return "no failed-step log (expired or job never ran)"
    if rerun_only and not raw.commits:
        # A pull request since merged into the default branch has no commits left after
        # it, so compare from the base the run itself tested instead.
        merge = MERGE_LINE.search(raw.failed_log)
        if merge is None or merge["head"] != c.red["head_sha"]:
            return "no commits under test (merged pull request, no merge line in the log)"
        commits = gh._compare(repo, merge["base"], merge["head"])
        raw = RawRun(run=raw.run, failed_log=raw.failed_log, commits=commits)
    if rerun_only and not raw.commits:
        return "no commits under test"
    if len(raw.commits) > max_commits:
        return f"{len(raw.commits)} commits under test (max {max_commits})"
    if rerun_only and len(raw.failed_log.encode()) > MAX_LOG_BYTES:
        return f"failed log over {MAX_LOG_BYTES} bytes"
    fix = (
        {"status": "rerun", "commits": [], "files": []}
        if c.flaky
        else gh.fix_files(repo, c.red["head_sha"], c.fixed["head_sha"])
    )
    if fix["status"] not in ("ahead", "rerun"):
        return f"history {fix['status']} between the red and green runs (force-push)"
    found = (
        Label(
            "infrastructure",
            None,
            "the first attempt failed and a re-run of the same pull-request run passed",
        )
        if rerun_only
        else label(c, raw, fix["files"], fix_merged=bool(fix.get("merges")))
    )
    if isinstance(found, str):
        return found
    case = case_from_raw(raw)
    service = repo.rsplit("/", 1)[-1]
    if found.guilty:
        evidence = [found.guilty[:10]]
    else:
        evidence = [log.id for log in case.store.logs if log.level == "error"][:5]
        if not evidence:
            return "no error log line to cite for a commit-free cause"
    scenario_id = f"ci-{service}-{c.red['id']}"
    case.scenario = case.scenario.model_copy(
        update={
            "id": scenario_id,
            "truth": GroundTruth(
                category=found.category, service=service, required_evidence_any=evidence
            ),
        }
    )
    target = out / scenario_id
    raw.save(target / "raw")
    (target / "raw" / "fix.json").write_text(
        json.dumps(
            {"fixed_run": c.fixed["html_url"], "fixed_sha": c.fixed["head_sha"], **fix}, indent=2
        )
    )
    note = (
        f"Mined from {c.red['html_url']} ({repo}, licence {meta['license']}). "
        f"Fixed by run {c.fixed['html_url']}. Label rule: {found.reason}. "
        f"Fix touched: {', '.join(fix['files'][:8]) or 'nothing (rerun)'}."
    )
    write_case(case, target, note=note)
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("repos", nargs="+")
    ap.add_argument("--since", required=True, help="YYYY-MM-DD; logs older than 90 days are gone")
    ap.add_argument("--out", type=Path, default=Path("scenarios-ci"))
    ap.add_argument("--max-commits", type=int, default=15)
    ap.add_argument("--max-per-repo", type=int, default=20)
    ap.add_argument("--event", choices=["pull_request", "push"], default="pull_request")
    ap.add_argument(
        "--rerun-only",
        action="store_true",
        help="only pull-request runs whose first attempt failed and a re-run passed",
    )
    ap.add_argument("--max-kept", type=int, default=0, help="stop a repository at this many")
    args = ap.parse_args()
    gh = GhCli()
    args.out.mkdir(parents=True, exist_ok=True)
    dropped = args.out / "dropped.jsonl"
    for repo in args.repos:
        meta = json.loads(
            gh.gh(
                ["api", f"repos/{repo}", "--jq", "{branch: .default_branch, license: "
                 '(.license.spdx_id // "none")}']
            )
        )  # fmt: skip
        if args.rerun_only:
            # Runs already mined or dropped into this --out are not fetched again.
            seen = (
                {json.loads(line)["run"] for line in dropped.open()} if dropped.exists() else set()
            )
            seen |= {p.name.rsplit("-", 1)[-1] for p in args.out.glob("ci-*")}
            found = [
                Candidate(green=None, red=r, fixed=r, rerun=True)
                for r in list_reruns(gh, repo, args.since)
                if r["html_url"] not in seen and str(r["id"]) not in seen
            ][: args.max_per_repo]
        else:
            found = sorted(
                candidates(list_runs(gh, repo, args.event, meta["branch"], args.since)),
                key=lambda c: c.red["created_at"],
                reverse=True,
            )[: args.max_per_repo]
        kept = 0
        for c in found:
            if args.max_kept and kept >= args.max_kept:
                break
            try:
                why = mine_one(
                    gh, repo, meta, c, args.out, args.max_commits, rerun_only=args.rerun_only
                )
            except Exception as e:  # one bad run must not stop the batch
                why = f"error: {type(e).__name__}: {str(e)[:200]}"
            if why is None:
                kept += 1
            else:
                with dropped.open("a") as f:
                    f.write(json.dumps({"repo": repo, "run": c.red["html_url"], "why": why}) + "\n")
        print(f"{repo}: {len(found)} breaks, {kept} kept, {len(found) - kept} dropped")


if __name__ == "__main__":
    main()
