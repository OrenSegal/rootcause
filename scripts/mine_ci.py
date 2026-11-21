"""Mine labelled CI-failure cases from public repositories (docs/ci-benchmark-scope.md).

For each repo: list completed push runs on the default branch since a date, find each
break (first red run after a green one, and the next green run), snapshot what `gh`
returns, label it with the rules in `adapters/github_actions.label`, and write a scenario.
Dropped breaks go to dropped.jsonl with the reason, so the drop rate can be reported.

    uv run python scripts/mine_ci.py pallets/flask pytest-dev/pytest --since 2026-08-25

Read-only `gh api` / `gh run view` calls only.
"""

import argparse
import json
from pathlib import Path
from typing import Any

from rootcause.adapters.fixtures import write_case
from rootcause.adapters.github_actions import Candidate, GhCli, candidates, case_from_raw, label
from rootcause.domain.scenario import GroundTruth


def push_runs(gh: GhCli, repo: str, branch: str, since: str) -> list[dict[str, Any]]:
    out = gh.gh(
        [
            "api",
            "--paginate",
            f"repos/{repo}/actions/runs?branch={branch}&event=push&status=completed"
            f"&created=>={since}&per_page=100",
            "--jq",
            ".workflow_runs[] | {id, name, workflow_id, conclusion, head_sha, created_at, "
            "run_attempt, html_url}",
        ]
    )
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def mine_one(
    gh: GhCli, repo: str, meta: dict[str, Any], c: Candidate, out: Path, max_commits: int
) -> str | None:
    """Writes the case and returns None, or returns why it was dropped."""
    if c.green is None and not c.flaky:
        return "no green run before the break inside the window"
    base = c.green["head_sha"] if c.green else None
    raw = gh.fetch(repo, c.red["id"], base_sha=base, attempt=1 if c.rerun else None)
    if not raw.failed_log.strip():
        return "no failed-step log (expired or job never ran)"
    if len(raw.commits) > max_commits:
        return f"{len(raw.commits)} commits under test (max {max_commits})"
    fix = (
        {"commits": [], "files": []}
        if c.flaky
        else gh.fix_files(repo, c.red["head_sha"], c.fixed["head_sha"])
    )
    found = label(c, raw.commits, fix["files"])
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
        found = sorted(
            candidates(push_runs(gh, repo, meta["branch"], args.since)),
            key=lambda c: c.red["created_at"],
            reverse=True,
        )[: args.max_per_repo]
        kept = 0
        for c in found:
            try:
                why = mine_one(gh, repo, meta, c, args.out, args.max_commits)
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
