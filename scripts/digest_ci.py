"""Print one block per mined case for the hand check: label, failed jobs, tested commits,
fix files and error lines. Usage: python scripts/digest_ci.py scenarios-ci/validate4"""

import json
import re
import sys
from pathlib import Path

import yaml

ANSI = re.compile(r"\x1b\[[0-9;]*m")
ERROR = re.compile(r"error|Error|FAILED|failed|assert")


def digest(case: Path) -> None:
    truth = yaml.safe_load((case / "scenario.yaml").read_text())["truth"]
    run = json.loads((case / "raw/run.json").read_text())
    commits = json.loads((case / "raw/commits.json").read_text())
    fix = json.loads((case / "raw/fix.json").read_text())
    failed = [j["name"] for j in run["jobs"] if j["conclusion"] == "failure"]
    evidence = (truth.get("required_evidence_any") or [])[:2]
    print(f"=== {case.name} [{run['headSha'][:8]} {run['name']}] {truth['category']} {evidence}")
    print("  failed:", failed[:5], f"(+{len(failed) - 5})" if len(failed) > 5 else "")
    for c in commits:
        files = [f["filename"] for f in c["files"]][:5]
        print(f"  tested {c['oid'][:10]} {c['messageHeadline'][:70]} :: {files}")
    print(f"  fix: {len(fix['commits'])} commits, files {fix['files'][:8]}")
    log = ANSI.sub("", (case / "raw/failed.log").read_text()).splitlines()
    for line in [x for x in log if ERROR.search(x)][:6]:
        print("   |", line.split("\t")[-1][29:200])


if __name__ == "__main__":
    for case in sorted(Path(sys.argv[1]).glob("ci-*")):
        digest(case)
