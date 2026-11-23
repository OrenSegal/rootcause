"""Freeze the reviewed CI failures in scenarios-ci/ into benchmarks/ci/{dev,test}.

Input: the mined cases in scenarios-ci/ (not committed) and benchmarks/ci/verdicts.csv,
written by hand from the verdict tables in docs/ci-benchmark-scope.md. A case goes in
only if its verdict is "right", it is not a duplicate, its stored label agrees with the
table, nothing the agent can read reveals the fix, and its log is under MAX_LOG_BYTES.

Output: one scenario directory per case, logs gzipped, split by repository into dev and
test; provenance (run.json, commits.json, fix.json and the mined scenario.yaml with its
comment header) under benchmarks/ci/provenance/, which no telemetry tool reads; labels.csv
and excluded.csv. The output depends only on the inputs, so a second run is identical.

Usage: uv run python scripts/freeze_ci_benchmark.py
"""

import csv
import gzip
import json
import math
import random
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scenarios-ci"
OUT = ROOT / "benchmarks" / "ci"
VERDICTS = OUT / "verdicts.csv"
ROUND_DIRS = {
    "m1-pr": "pr",
    "v1": "validate",
    "v2": "validate2",
    "v3": "validate3",
    "v4": "validate4",
}
UNREVIEWED_DIRS = {"main": "default-branch run (excluded)", "validate5": "never reviewed"}
SEED = 20261123
DEV_SHARE = 0.25
# Cases whose logs.jsonl is larger than this are left out rather than truncated.
MAX_LOG_BYTES = 40_000_000
AGENT_FILES = ("metrics.json", "traces.json", "deploys.json")
PROVENANCE_FILES = ("run.json", "commits.json", "fix.json")
NOT_CHANGE_CATEGORIES = {"infrastructure", "dependency_failure"}
CHANGE_CATEGORIES = {"bad_code_deploy", "config_change"}


def header(text: str) -> str:
    """The comment header of a mined scenario.yaml as one line. textwrap broke long words
    after a hyphen, so a line ending in "-" joins the next without a space."""
    out = ""
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        part = line[1:].strip()
        out += part if out.endswith("-") or not out else " " + part
    return out


def label_problem(row: dict[str, str], truth: dict[str, Any], deploy_ids: set[str]) -> str | None:
    """Why the stored label disagrees with the verdict table, or None."""
    category, evidence = truth["category"], truth["required_evidence_any"]
    cited = [e for e in evidence if e in deploy_ids]
    if row["table_class"] == "not_change":
        if category not in NOT_CHANGE_CATEGORIES or cited:
            return f"table says not caused by a change; stored {category} {cited}"
    elif category not in CHANGE_CATEGORIES or len(evidence) != 1 or not cited:
        return f"table says caused by a change; stored {category} {evidence}"
    if row["table_category"] and category != row["table_category"]:
        return f"table says {row['table_category']}; stored {category}"
    if row["table_commit"] and not evidence[0].startswith(row["table_commit"]):
        return f"table names commit {row['table_commit']}; stored {evidence[0]}"
    return None


def visible_text(case: Path, scenario: dict[str, Any]) -> str:
    """Everything the agent can read: the alert, the title and the four fixture files."""
    parts = [json.dumps([scenario["title"], scenario["alert"]])]
    parts += [(case / name).read_text() for name in ("logs.jsonl", *AGENT_FILES)]
    return "\n".join(parts)


def leaks(text: str, traces: list[dict[str, Any]], fix: dict[str, Any], run_id: str) -> list[str]:
    """What in `text` reveals the fix or the green run. A rerun's green commit is the
    failed commit itself, and its green run is the same run id, so those are not checked."""
    found = []
    if fix.get("status") != "rerun":
        for sha in {fix["fixed_sha"], *fix["commits"]}:
            if sha[:10] in text:
                found.append(f"fix commit {sha[:10]}")
    green_run = fix["fixed_run"].rsplit("/", 1)[-1]
    if green_run != run_id and green_run in text:
        found.append(f"green run {green_run}")
    root = next(s for s in traces if s["parent_id"] is None)
    if root["status"] != "error":
        # GitHub overwrites the first attempt's conclusion when a rerun passes.
        found.append(f"trace root span status is {root['status']!r}")
    return found


def split(rows: list[dict[str, str]]) -> dict[str, str]:
    """Repository -> "dev" or "test". Repositories are shuffled with a fixed seed and added
    to dev until it holds about DEV_SHARE of the cases. The first shuffle is used in which
    each class has at least one case in dev and in test, and dev holds at most one case
    more than DEV_SHARE of that class."""
    per_repo = Counter(r["repo"] for r in rows)
    per_class = Counter(r["class"] for r in rows)
    target = round(len(rows) * DEV_SHARE)
    rng = random.Random(SEED)
    for _ in range(10_000):
        order = sorted(per_repo)
        rng.shuffle(order)
        dev: set[str] = set()
        for repo in order:
            if sum(per_repo[r] for r in dev) >= target:
                break
            dev.add(repo)
        size = sum(per_repo[r] for r in dev)
        in_dev = Counter(r["class"] for r in rows if r["repo"] in dev)
        fits = all(
            1 <= in_dev[c] < n and in_dev[c] <= math.ceil(n * DEV_SHARE) + 1
            for c, n in per_class.items()
        )
        if size - target <= 2 and fits:
            return {repo: "dev" if repo in dev else "test" for repo in per_repo}
    raise SystemExit("no split meets the class constraints")


def main() -> None:
    verdicts = list(csv.DictReader(VERDICTS.open()))
    excluded: list[tuple[str, str, str]] = []
    kept: list[dict[str, str]] = []
    for row in verdicts:
        cid = row["case_id"]
        case = SOURCE / ROUND_DIRS[row["round"]] / cid
        if not (case / "scenario.yaml").exists():
            excluded.append((cid, row["round"], "verdict-table entry not found on disk"))
            continue
        if row["verdict"] != "right":
            excluded.append((cid, row["round"], f"verdict {row['verdict']}"))
            continue
        if row["duplicate_of"]:
            excluded.append((cid, row["round"], f"duplicate of {row['duplicate_of']}"))
            continue
        mined = (case / "scenario.yaml").read_text()
        scenario = yaml.safe_load(mined)
        deploys = json.loads((case / "deploys.json").read_text())
        traces = json.loads((case / "traces.json").read_text())
        fix = json.loads((case / "raw" / "fix.json").read_text())
        run = json.loads((case / "raw" / "run.json").read_text())
        run_id = str(run["databaseId"])
        problem = label_problem(row, scenario["truth"], {d["id"] for d in deploys})
        if problem:
            excluded.append((cid, row["round"], f"label disagrees with table: {problem}"))
            continue
        found = leaks(visible_text(case, scenario), traces, fix, run_id)
        if found:
            excluded.append((cid, row["round"], "leak: " + "; ".join(found)))
            continue
        size = (case / "logs.jsonl").stat().st_size
        if size > MAX_LOG_BYTES:
            excluded.append((cid, row["round"], f"oversized: logs.jsonl is {size} bytes"))
            continue
        note = header(mined)
        licence = re.search(r"licence (\S+?)\)", note)
        url = re.search(r"Mined from (\S+)", note)
        assert licence and url and url[1].endswith(f"/actions/runs/{run_id}"), cid
        truth = scenario["truth"]
        change = truth["category"] in CHANGE_CATEGORIES
        kept.append(
            {
                "id": cid,
                "repo": row["repo"],
                "licence": licence[1],
                "round": row["round"],
                "category": truth["category"],
                "guilty_commit": truth["required_evidence_any"][0] if change else "",
                "class": "change" if change else "not_change",
                "split": "",
                "commits_under_test": str(len(deploys)),
                "head_commit": run["headSha"][:10],
                "run_url": url[1],
                "_dir": str(case),
            }
        )
    for name, reason in UNREVIEWED_DIRS.items():
        for case in sorted((SOURCE / name).glob("ci-*")):
            excluded.append((case.name, name, reason))

    by_repo = split(kept)
    for name in ("dev", "test", "provenance"):
        shutil.rmtree(OUT / name, ignore_errors=True)
    for row in kept:
        row["split"] = by_repo[row["repo"]]
        src = Path(row.pop("_dir"))
        dst = OUT / row["split"] / row["id"]
        dst.mkdir(parents=True)
        mined = (src / "scenario.yaml").read_text()
        body = "".join(line for line in mined.splitlines(keepends=True) if not line.startswith("#"))
        (dst / "scenario.yaml").write_text(body)
        logs = (src / "logs.jsonl").read_bytes()
        (dst / "logs.jsonl.gz").write_bytes(gzip.compress(logs, compresslevel=9, mtime=0))
        for name in AGENT_FILES:
            shutil.copyfile(src / name, dst / name)
        prov = OUT / "provenance" / row["id"]
        prov.mkdir(parents=True)
        (prov / "scenario.mined.yaml").write_text(mined)
        for name in PROVENANCE_FILES:
            shutil.copyfile(src / "raw" / name, prov / name)

    kept.sort(key=lambda r: (r["split"], r["id"]))
    with (OUT / "labels.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(kept[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(kept)
    with (OUT / "excluded.csv").open("w", newline="") as f:
        out = csv.writer(f, lineterminator="\n")
        out.writerow(["case_id", "round", "reason"])
        excluded.sort(key=lambda e: (e[1], e[0]))
        out.writerows(excluded)
    summarise(kept, excluded)


def summarise(kept: list[dict[str, str]], excluded: list[tuple[str, str, str]]) -> None:
    """Counts for the data card."""
    print(f"included {len(kept)}, excluded {len(excluded)}")
    for (rnd, reason), n in sorted(
        Counter((e[1], e[2].split(":")[0].split(" of ")[0]) for e in excluded).items()
    ):
        print(f"  excluded {rnd:10} {reason:45} {n}")
    for rnd, n in sorted(Counter(r["round"] for r in kept).items()):
        print(f"  included {rnd:10} {n}")
    for s in ("dev", "test"):
        rows = [r for r in kept if r["split"] == s]
        print(
            f"{s}: {len(rows)} cases, {len({r['repo'] for r in rows})} repos,",
            dict(Counter(r["class"] for r in rows)),
        )
    one = sum(r["commits_under_test"] == "1" for r in kept)
    print(f"one commit under test: {one} of {len(kept)}")
    change = [r for r in kept if r["class"] == "change"]
    head = sum(r["guilty_commit"] == r["head_commit"] for r in change)
    print(f"head commit guilty: {head} of {len(change)} change cases")


if __name__ == "__main__":
    main()
