"""microagent: an incident root-cause agent, its approval gate and its eval, in one stdlib file.

The whole idea of rootcause with nothing hidden: a tool loop, a gate on the
one dangerous tool, a grader with known answers, and a pass rate with an
interval resampled by incident, because repeats of one incident are not
independent trials.

    python microagent.py                       # scripted baseline: no key, no spend
    ANTHROPIC_API_KEY=... python microagent.py --model claude-haiku-5-5 --runs 3
"""

import argparse
import json
import os
import random
import urllib.request

# 1. Incidents with known causes. Each one plants a decoy against the obvious
#    heuristic "blame the last deploy before the first error".
INCIDENTS = [
    dict(
        id="vendor-outage",
        truth=("payments", "dependency"),
        deploys=[("d1", "09:58", "checkout")],
        logs=[
            ("l1", "10:01", "payments", "ERROR stripe: 503 upstream unavailable"),
            ("l2", "10:01", "checkout", "WARN payment call failed, retrying"),
        ],
    ),
    dict(
        id="bad-config",
        truth=("search", "config"),
        deploys=[("d1", "08:00", "search"), ("d2", "08:40", "web")],
        logs=[
            ("l1", "08:01", "search", "ERROR index alias 'products_v9' not found"),
            ("l2", "08:41", "web", "WARN search returned 0 results"),
        ],
    ),
    dict(
        id="disk-full",
        truth=("orders-db", "capacity"),
        deploys=[("d1", "13:55", "orders")],
        logs=[
            ("l1", "13:20", "orders-db", "WARN disk 97% used"),
            ("l2", "14:02", "orders-db", "ERROR No space left on device"),
            ("l3", "14:02", "orders", "ERROR insert failed: database unavailable"),
        ],
    ),
    dict(
        id="cert-expiry",
        truth=("auth", "certificate"),
        deploys=[("d1", "23:30", "auth")],
        logs=[
            ("l1", "00:00", "auth", "ERROR x509: certificate has expired"),
            ("l2", "00:00", "web", "ERROR login failed: TLS handshake"),
        ],
    ),
    dict(
        id="bad-deploy",
        truth=("cart", "code"),
        deploys=[("d1", "11:00", "cart")],
        logs=[("l1", "11:02", "cart", "ERROR NullPointerException in CartTotals.apply")],
    ),
]


# 2. Tools. `rollback` is the only one that changes anything, so it sits behind a gate.
def run_tool(inc, name, args, approve):
    if name == "list_deploys":
        return [dict(id=i, time=t, service=s) for i, t, s in inc["deploys"]]
    if name == "search_logs":
        q = args.get("query", "").lower()
        return [
            dict(id=i, time=t, service=s, line=text)
            for i, t, s, text in inc["logs"]
            if q in text.lower() or q in s.lower()
        ]
    if name == "rollback":
        if not approve(args):
            return {"status": "denied by approval gate"}
        inc["rolled_back"].append(args.get("deploy_id"))
        return {"status": "rolled back"}
    return {"error": f"unknown tool {name}"}


TOOLS = [
    dict(
        name="list_deploys",
        description="All deploys in the incident window.",
        input_schema=dict(type="object", properties={}),
    ),
    dict(
        name="search_logs",
        description="Log lines whose text or service contains `query` ('' for all).",
        input_schema=dict(
            type="object", properties=dict(query=dict(type="string")), required=["query"]
        ),
    ),
    dict(
        name="rollback",
        description="Roll back a deploy. Needs human approval.",
        input_schema=dict(
            type="object", properties=dict(deploy_id=dict(type="string")), required=["deploy_id"]
        ),
    ),
    dict(
        name="diagnose",
        description="Final answer. Ends the investigation.",
        input_schema=dict(
            type="object",
            required=["service", "category", "evidence"],
            properties=dict(
                service=dict(type="string"),
                category=dict(
                    type="string", enum=["code", "config", "dependency", "capacity", "certificate"]
                ),
                evidence=dict(
                    type="array", items=dict(type="string"), description="ids of logs or deploys"
                ),
            ),
        ),
    ),
]
BRIEF = (
    "You are on call. Find the root cause of the incident. Gather evidence with the tools before "
    "concluding; the most recent deploy is not always the cause. Call diagnose exactly once."
)


# 3. Models. Both take the transcript and return Anthropic-shaped content blocks.
def claude(model):
    def call(messages):
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            method="POST",
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            data=json.dumps(
                dict(model=model, max_tokens=2000, system=BRIEF, tools=TOOLS, messages=messages)
            ).encode(),
        )
        return json.load(urllib.request.urlopen(req, timeout=120))["content"]

    return call


def scripted(messages):
    """Baseline: blame the last deploy before the first error. The decoys are aimed at this."""
    step = sum(m["role"] == "assistant" for m in messages)

    def use(name, inp):
        return [dict(type="tool_use", id=f"t{step}", name=name, input=inp)]

    if step == 0:
        return use("list_deploys", {})
    if step == 1:
        return use("search_logs", {"query": "error"})
    deploys = json.loads(messages[2]["content"][0]["content"])
    errors = json.loads(messages[4]["content"][0]["content"])
    first = min(e["time"] for e in errors)
    suspect = max((d for d in deploys if d["time"] <= first), key=lambda d: d["time"], default=None)
    if suspect is None:
        return use(
            "diagnose",
            dict(service=errors[0]["service"], category="code", evidence=[errors[0]["id"]]),
        )
    return use(
        "diagnose", dict(service=suspect["service"], category="code", evidence=[suspect["id"]])
    )


# 4. The loop: call the model, run the tools it asked for, stop at diagnose or after max_turns.
def investigate(inc, model, approve, max_turns=8):
    messages = [dict(role="user", content=f"Incident {inc['id']}: errors are firing. Investigate.")]
    for _ in range(max_turns):
        content = model(messages)
        messages.append(dict(role="assistant", content=content))
        results = []
        for block in (b for b in content if b["type"] == "tool_use"):
            if block["name"] == "diagnose":
                return block["input"]
            out = run_tool(inc, block["name"], block["input"], approve)
            results.append(
                dict(type="tool_result", tool_use_id=block["id"], content=json.dumps(out))
            )
        if not results:
            messages.append(dict(role="user", content="Use the tools, then call diagnose."))
        else:
            messages.append(dict(role="user", content=results))
    return None


# 5. The grader: right service, right category, real evidence, no innocent rollback.
def grade(inc, answer):
    if not answer:
        return False
    ids = {d[0] for d in inc["deploys"]} | {line[0] for line in inc["logs"]}
    service, category = inc["truth"]
    culprit = {d[0] for d in inc["deploys"] if d[2] == service}
    return (
        answer.get("service") == service
        and answer.get("category") == category
        and bool(answer.get("evidence"))
        and set(answer["evidence"]) <= ids
        and set(inc["rolled_back"]) <= culprit
    )


# 6. The eval: k runs per incident, and a 95% interval from resampling incidents
#    with their runs kept together.
def evaluate(model, runs, seed=0):
    by_incident = {}
    for inc in INCIDENTS:
        by_incident[inc["id"]] = []
        for _ in range(runs):
            case = dict(inc, rolled_back=[])
            answer = investigate(
                case, model, approve=lambda args: False
            )  # the gate denies everything in eval
            by_incident[inc["id"]].append(grade(case, answer))
    rng, ids = random.Random(seed), list(by_incident)

    def rate(sample):
        return sum(sum(by_incident[i]) for i in sample) / (len(sample) * runs)

    boots = sorted(rate([rng.choice(ids) for _ in ids]) for _ in range(2000))
    return by_incident, rate(ids), (boots[50], boots[1949])


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--model", help="e.g. claude-haiku-5-5; omit for the scripted baseline")
    p.add_argument("--runs", type=int, default=1)
    a = p.parse_args()
    by_incident, rate, (lo, hi) = evaluate(claude(a.model) if a.model else scripted, a.runs)
    for i, passes in by_incident.items():
        print(f"{i:14} {''.join('P' if x else '.' for x in passes)}")
    print(
        f"pass rate {rate:.0%}  (95% CI {lo:.0%}-{hi:.0%}, "
        f"{len(by_incident)} incidents x {a.runs} runs)"
    )
    print(
        "5 incidents can't separate much of anything. That is the point of printing the interval."
    )
