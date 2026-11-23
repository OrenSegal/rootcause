"""Freeze the reviewed CI failures in scenarios-ci/ into benchmarks/ci/{dev,test}.

Input: the mined cases in scenarios-ci/ (not committed) and benchmarks/ci/verdicts.csv,
written by hand from the verdict tables in docs/ci-benchmark-scope.md and from the review
of the re-run cases in scenarios-ci/rerun/. Each row's label_source says where its label
comes from:

- rule: the mining rules proposed it and the review recorded it as right. The stored label
  must agree with the table.
- review: the review recorded the rule's label as wrong and the scope document states the
  right one, which verdicts.csv copies (class, category, commit). The truth is rebuilt
  from those columns.
- rerun: the first attempt of a pull-request run failed and a re-run of the same run
  passed. The miner labels it infrastructure with no commit; the review checked the log.

A case goes in only if it has a label from one of these, it is not a duplicate of an
included case, nothing the agent can read reveals the fix, its log is under MAX_LOG_BYTES,
and its repository has not reached MAX_RERUN_PER_REPO re-run cases.

Output: one scenario directory per case, logs gzipped, split by repository into dev and
test; provenance (run.json.gz, commits.json, fix.json and the mined scenario.yaml with its
comment header) under benchmarks/ci/provenance/, which no telemetry tool reads; labels.csv
and excluded.csv. The output depends only on the inputs, so a second run is identical.

Usage: uv run python scripts/freeze_ci_benchmark.py
"""

import csv
import gzip
import json
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
    "rerun": "rerun",
}
UNREVIEWED_DIRS = {"main": "default-branch run (excluded)", "validate5": "never reviewed"}
SEED = 20261123
# The share of each class that goes to dev. not_change is scarce, so dev gets more of it.
DEV_SHARE = {"change": 0.25, "not_change": 1 / 3}
# How far dev may be from its target, in cases, per class.
DEV_SLACK = 2
# Re-run cases kept per repository (lowest run ids first), so no one repository dominates.
MAX_RERUN_PER_REPO = 4
# How many error-level log lines a not_change truth cites, as scripts/mine_ci.py does.
EVIDENCE_LINES = 5
# Cases whose logs.jsonl is larger than this are left out rather than truncated.
MAX_LOG_BYTES = 40_000_000
AGENT_FILES = ("metrics.json", "traces.json", "deploys.json")
PROVENANCE_FILES = ("commits.json", "fix.json")
# run.json (every job and step) is most of the provenance by size, so it is kept gzipped.
GZIPPED_PROVENANCE = ("run.json",)
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


def has_label(row: dict[str, str]) -> bool:
    """A rule or re-run label the review recorded as right, or a label the review stated."""
    return row["verdict"] == "right" or row["label_source"] == "review"


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


def reviewed_truth(row: dict[str, str], case: Path, service: str) -> dict[str, Any] | str:
    """The truth a review row states: the guilty commit for a change, or the first
    error-level log lines (as the miner cites them) for a not_change case. A string says
    why it cannot be built."""
    deploy_ids = [d["id"] for d in json.loads((case / "deploys.json").read_text())]
    if row["table_class"] == "change":
        commit = row["table_commit"]
        if row["table_category"] not in CHANGE_CATEGORIES or commit not in deploy_ids:
            return f"review names {row['table_category']} on {commit}, not a tested commit"
        evidence = [commit]
    else:
        lines = (json.loads(x) for x in (case / "logs.jsonl").read_text().splitlines())
        evidence = [x["id"] for x in lines if x["level"] == "error"][:EVIDENCE_LINES]
        if not evidence:
            return "no error log line to cite for a commit-free cause"
    category = row["table_category"]
    return {"category": category, "service": service, "required_evidence_any": evidence}


def with_truth(body: str, truth: dict[str, Any]) -> str:
    """The scenario.yaml body with its truth replaced, dumped as write_case dumps it."""
    scenario = yaml.safe_load(body)
    scenario["truth"] = truth
    return yaml.safe_dump(scenario, sort_keys=False, width=88)


def repaired_traces(traces: list[dict[str, Any]], fix: dict[str, Any]) -> list[dict[str, Any]]:
    """When failed jobs are re-run and pass, GitHub can rewrite the first attempt's own
    conclusion to success, while its jobs keep theirs. The root span then says the run
    passed, which gives away that a re-run did (pytest 42785732184). For re-run cases the
    root span says error when any job failed, which is what the attempt itself did."""
    if fix.get("status") != "rerun":
        return traces
    root = next(s["span_id"] for s in traces if s["parent_id"] is None)
    failed = any(s["status"] == "error" for s in traces if s["parent_id"] == root)
    return [s | {"status": "error"} if s["span_id"] == root and failed else s for s in traces]


def dump_traces(traces: list[dict[str, Any]]) -> str:
    """traces.json as write_case writes it."""
    return json.dumps(traces, indent=2) + "\n"


def visible_text(case: Path, scenario: dict[str, Any], traces: list[dict[str, Any]]) -> str:
    """Everything the agent can read: the alert, the title and the four fixture files."""
    parts = [json.dumps([scenario["title"], scenario["alert"]]), dump_traces(traces)]
    parts += [(case / name).read_text() for name in ("logs.jsonl", "metrics.json", "deploys.json")]
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
    """Repository -> "dev" or "test". Repositories are shuffled with a fixed seed and each,
    in that order, goes to dev if dev would then stay within DEV_SLACK cases of its target
    for every class (DEV_SHARE of that class). The first shuffle whose dev lands within
    DEV_SLACK of the target for every class is used."""
    per_repo: dict[str, Counter[str]] = {}
    for r in rows:
        per_repo.setdefault(r["repo"], Counter())[r["class"]] += 1
    per_class = Counter(r["class"] for r in rows)
    target = {c: n * DEV_SHARE[c] for c, n in per_class.items()}
    rng = random.Random(SEED)
    for _ in range(10_000):
        order = sorted(per_repo)
        rng.shuffle(order)
        dev: set[str] = set()
        in_dev: Counter[str] = Counter()
        for repo in order:
            if all(in_dev[c] + per_repo[repo][c] <= target[c] + DEV_SLACK for c in target):
                dev.add(repo)
                in_dev += per_repo[repo]
        if all(abs(in_dev[c] - target[c]) <= DEV_SLACK for c in target):
            return {repo: "dev" if repo in dev else "test" for repo in per_repo}
    raise SystemExit("no split meets the class constraints")


def main() -> None:
    verdicts = list(csv.DictReader(VERDICTS.open()))
    labelled = {r["case_id"] for r in verdicts if has_label(r)}
    excluded: list[tuple[str, str, str]] = []
    kept: list[dict[str, str]] = []
    reruns: Counter[str] = Counter()
    # Lowest run id first within a round, so the per-repository cap keeps a fixed set.
    for row in sorted(verdicts, key=lambda r: (r["round"], r["repo"], r["case_id"])):
        cid = row["case_id"]
        case = SOURCE / ROUND_DIRS[row["round"]] / cid
        if not (case / "scenario.yaml").exists():
            excluded.append((cid, row["round"], "verdict-table entry not found on disk"))
            continue
        if not has_label(row):
            excluded.append((cid, row["round"], f"verdict {row['verdict']}"))
            continue
        if row["duplicate_of"] in labelled:
            # A duplicate of a case that is itself left out stands on its own.
            excluded.append((cid, row["round"], f"duplicate of {row['duplicate_of']}"))
            continue
        mined = (case / "scenario.yaml").read_text()
        scenario = yaml.safe_load(mined)
        deploys = json.loads((case / "deploys.json").read_text())
        fix = json.loads((case / "raw" / "fix.json").read_text())
        traces = repaired_traces(json.loads((case / "traces.json").read_text()), fix)
        run = json.loads((case / "raw" / "run.json").read_text())
        run_id = str(run["databaseId"])
        service = row["repo"].rsplit("/", 1)[-1]
        if row["label_source"] == "review":
            truth = reviewed_truth(row, case, service)
            if isinstance(truth, str):
                excluded.append((cid, row["round"], f"review label cannot be built: {truth}"))
                continue
        else:
            truth = scenario["truth"]
            problem = label_problem(row, truth, {d["id"] for d in deploys})
            if problem:
                excluded.append((cid, row["round"], f"label disagrees with table: {problem}"))
                continue
        found = leaks(visible_text(case, scenario, traces), traces, fix, run_id)
        if found:
            excluded.append((cid, row["round"], "leak: " + "; ".join(found)))
            continue
        size = (case / "logs.jsonl").stat().st_size
        if size > MAX_LOG_BYTES:
            excluded.append((cid, row["round"], f"oversized: logs.jsonl is {size} bytes"))
            continue
        if row["label_source"] == "rerun":
            if reruns[row["repo"]] >= MAX_RERUN_PER_REPO:
                excluded.append(
                    (cid, row["round"], f"over the cap of {MAX_RERUN_PER_REPO} per repository")
                )
                continue
            reruns[row["repo"]] += 1
        note = header(mined)
        licence = re.search(r"licence (\S+?)\)", note)
        url = re.search(r"Mined from (\S+)", note)
        assert licence and url and url[1].endswith(f"/actions/runs/{run_id}"), cid
        change = truth["category"] in CHANGE_CATEGORIES
        kept.append(
            {
                "id": cid,
                "repo": row["repo"],
                "licence": licence[1],
                "round": row["round"],
                "label_source": row["label_source"],
                "category": truth["category"],
                "guilty_commit": truth["required_evidence_any"][0] if change else "",
                "class": "change" if change else "not_change",
                "split": "",
                "commits_under_test": str(len(deploys)),
                "head_commit": run["headSha"][:10],
                "run_url": url[1],
                "_dir": str(case),
                "_truth": json.dumps(truth),
                "_traces": dump_traces(traces),
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
        truth, traces = json.loads(row.pop("_truth")), row.pop("_traces")
        dst = OUT / row["split"] / row["id"]
        dst.mkdir(parents=True)
        mined = (src / "scenario.yaml").read_text()
        body = "".join(line for line in mined.splitlines(keepends=True) if not line.startswith("#"))
        if truth != yaml.safe_load(body)["truth"]:
            body = with_truth(body, truth)
        # Grade on change versus not change and the commit, not the exact category.
        assert body.count("\ntruth:\n") == 1, row["id"]
        body = body.replace("\ntruth:\n", "\ntruth:\n  grading: class\n")
        (dst / "scenario.yaml").write_text(body)
        logs = (src / "logs.jsonl").read_bytes()
        (dst / "logs.jsonl.gz").write_bytes(gzip.compress(logs, compresslevel=9, mtime=0))
        for name in AGENT_FILES:
            shutil.copyfile(src / name, dst / name)
        (dst / "traces.json").write_text(traces)
        prov = OUT / "provenance" / row["id"]
        prov.mkdir(parents=True)
        (prov / "scenario.mined.yaml").write_text(mined)
        for name in PROVENANCE_FILES:
            shutil.copyfile(src / "raw" / name, prov / name)
        for name in GZIPPED_PROVENANCE:
            raw = (src / "raw" / name).read_bytes()
            (prov / f"{name}.gz").write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))

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
    print("by label source:", dict(Counter((r["label_source"], r["class"]) for r in kept)))
    one = sum(r["commits_under_test"] == "1" for r in kept)
    print(f"one commit under test: {one} of {len(kept)}")
    # Blaming the head commit passes a change case when the head is guilty, and never
    # passes a not_change case, where citing any commit fails.
    for s in ("dev", "test"):
        change = [r for r in kept if r["class"] == "change" and r["split"] == s]
        head = sum(r["guilty_commit"] == r["head_commit"] for r in change)
        print(f"{s}: head commit guilty in {head} of {len(change)} change cases")


if __name__ == "__main__":
    main()
