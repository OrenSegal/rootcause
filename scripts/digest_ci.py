"""Print one block per mined case for the hand check: label, failed jobs, tested commits,
fix files and error lines. Usage: python scripts/digest_ci.py scenarios-ci/validate4"""

import json, re, sys, glob, yaml

ANSI = re.compile(r"\x1b\[[0-9;]*m")
for d in sorted(glob.glob(sys.argv[1] + "/ci-*")):
    s = yaml.safe_load(open(d + "/scenario.yaml"))
    r = json.load(open(d + "/raw/run.json"))
    c = json.load(open(d + "/raw/commits.json"))
    f = json.load(open(d + "/raw/fix.json"))
    hdr = open(d + "/scenario.yaml").read().split("\nid:")[0]
    rule = re.search(r"Label rule: (.*?)\.", hdr.replace("\n# ", " "), re.S)
    t = s["truth"]
    ev = t.get("required_evidence_any") or []
    failed = [j["name"] for j in r["jobs"] if j["conclusion"] == "failure"]
    print(
        f"=== {d} [{r['headSha'][:8]} {r['name']}] {t['category']} ev={ev[:2]} rule={rule[1] if rule else ''}"
    )
    print("  failed:", failed[:5], f"(+{len(failed) - 5})" if len(failed) > 5 else "")
    for x in c:
        print(
            f"  tested {x['oid'][:10]} {x['messageHeadline'][:70]} :: {[y['filename'] for y in x['files']][:5]}"
        )
    print(
        "  fix commits:",
        [
            x.get("messageHeadline", x)[:60] if isinstance(x, dict) else str(x)[:60]
            for x in f["commits"]
        ][:4],
    )
    print("  fix files:", f["files"][:8])
    log = ANSI.sub("", open(d + "/raw/failed.log").read()).splitlines()
    errs = [
        l.split("\t")[-1][29:200] for l in log if re.search(r"error|Error|FAILED|failed|assert", l)
    ]
    for e in errs[:6]:
        print("   |", e)
