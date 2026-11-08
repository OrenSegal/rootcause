"""The externally sourced held-out set, as code. Regenerate the fixtures with:

    uv run python scripts/author_external.py

Each scenario adapts one public postmortem. The cause, the category and the service
come from the postmortem, not from the author of the main suite; the telemetry that
renders it is still written here. Company names, hostnames, dates and scale are
changed and timelines compressed, so a model cannot match an incident by name.

Every truth below, including accepted services, was fixed on 2026-11-07 and
committed before anything ran on these scenarios, the heuristic included. It is
never widened after an answer is read. Results on this set are never used to change
the brief, the grader or an accepted set. tests/test_scenarios.py fails if the
committed fixtures in scenarios-external/ differ from this file.
"""

from pathlib import Path

from rootcause.adapters.scenario_builder import ScenarioBuilder
from rootcause.domain.scenario import Alert, Category

C = Category
# What each source page states, checked 2026-11-07. None grants an open licence.
LICENCES = {
    "Cloudflare": "no licence or copyright notice on the page",
    "Fastly": "copyright notice (\u00a9 Fastly), no licence granted",
    "Mailchimp/Mandrill": "copyright, all rights reserved, no licence granted",
    "Slack": "copyright, all rights reserved, no licence granted",
    "Datadog": "copyright notice (\u00a9 Datadog), no licence granted",
    "GitHub": "copyright notice (\u00a9 GitHub, Inc.), no licence granted",
    "Amazon Web Services": "copyright, all rights reserved, no licence granted",
}
FROZEN = "Truth frozen 2026-11-07, before any run on this set, the heuristic included."


def note(what: str, source: str, runner_up: str, accepted: str) -> str:
    return (
        f"{what} Source: {source}. Licence: "
        f"{next(v for k, v in LICENCES.items() if f'({k},' in source)}. Facts are "
        "paraphrased, no text is reproduced, and the telemetry is original. "
        f"Runner-up reading: {runner_up}. "
        f"Accepted services: {accepted}. Names, dates and scale changed; timeline "
        f"compressed. {FROZEN}"
    )


def waf_regex() -> ScenarioBuilder:
    """Cloudflare, 2019-07-02: a managed WAF rule with a backtracking regex, pushed
    globally at once, pinned every proxy CPU and turned traffic into 502s."""
    b = ScenarioBuilder(
        "x01-waf-regex-backtracking",
        "A global WAF rule push pins proxy CPU",
        Alert(service="edge", summary="HTTP 502 rate at 80% in every region; edge CPU near 100%."),
        note=note(
            "A managed-rules update to the WAF carried a regular expression that backtracks "
            "catastrophically; it went to every site at once and exhausted CPU on the edge "
            "proxies, which returned 502s. The change is a ruleset version in the WAF's "
            "config, so the category is config_change.",
            "https://blog.cloudflare.com/details-of-the-cloudflare-outage-on-july-2-2019/ "
            "(Cloudflare, 2019-07-02)",
            "bad_code_deploy (a rule is code-like); resource_exhaustion (CPU is the symptom)",
            "none (the rule lives in the waf service, which has its own deploy and logs)",
        ),
    )
    b.deploy("d-E100", "dashboard", "3.8.0", -30, "Analytics tab: new filters", author="ines")
    bad = b.deploy(
        "d-E101",
        "waf",
        "rules-2026.10.14.1",
        20,
        "Managed rules: improved XSS detection (simulate mode), global push",
        author="rules-ci",
        before={"WAF_MANAGED_RULESET": "2026.10.13.4"},
        after={"WAF_MANAGED_RULESET": "2026.10.14.1"},
    )
    for m in range(0, 20, 4):
        b.log(m, "edge", "info", "served 1.2M req/s, p99 41ms")
    b.log(20.1, "waf", "info", "loaded managed ruleset 2026.10.14.1 (412 rules, mode=simulate)")
    first = None
    for m in [20.4, 21, 22, 24, 27, 31]:
        b.log(
            m - 0.1,
            "waf",
            "warn",
            "rule 100035 evaluation took 1840ms on one request (limit 50ms); pcre backtracking",
        )
        log_id = b.log(m, "edge", "error", "worker CPU saturated, request timed out; returned 502")
        first = first or log_id
    b.step_series("m-edge-cpu", "edge", "cpu_utilization", "ratio", 0.32, 0.99, 20.4)
    b.step_series("m-edge-5xx", "edge", "http_5xx_rate", "ratio", 0.001, 0.8, 20.4)
    rule_ms = b.step_series("m-waf-eval", "waf", "rule_eval_p99", "ms", 0.4, 1840, 20.2)
    assert first
    b.truth(C.CONFIG_CHANGE, "waf", b.config_id(bad, "WAF_MANAGED_RULESET"), bad, rule_ms)
    return b


def latent_bug() -> ScenarioBuilder:
    """Fastly, 2021-06-08: a valid customer configuration change triggered a bug
    shipped in an edge software release weeks earlier."""
    b = ScenarioBuilder(
        "x02-latent-bug-valid-config",
        "A valid tenant config trips a bug shipped days earlier",
        Alert(service="edge", summary="error rate at 85% across the network."),
        note=note(
            "An edge runtime release (d-E201) shipped a bug in a new routing feature. Days "
            "later a valid tenant configuration (d-E202, validated and published normally) "
            "met the conditions that trigger it and edge workers crashed everywhere. The "
            "postmortem names the software bug as the root cause and the config as the "
            "trigger, so the category is bad_code_deploy and the service edge. The real gap "
            "was 27 days; here it is 3 days.",
            "https://www.fastly.com/blog/summary-of-june-8-outage (Fastly, 2021-06-08)",
            "config_change (blaming the trigger, d-E202)",
            "none (edge is both where the bug runs and where the config is applied)",
        ),
    )
    bad = b.deploy(
        "d-E201",
        "edge",
        "7.4.0",
        -4320,
        "Edge runtime 7.4.0: request routing for tenant-defined path rules",
        author="edge-release",
    )
    b.log(-4310, "edge", "info", "edge 7.4.0 rolled out to 100% of nodes")
    b.deploy(
        "d-E202",
        "edge",
        "tenant-cfg-4417-v13",
        19,
        "Tenant 4417: update path routing rules",
        author="tenant-api",
        before={"TENANT_4417_PATH_RULES": "v12"},
        after={"TENANT_4417_PATH_RULES": "v13"},
    )
    for m in range(0, 19, 4):
        b.log(m, "edge", "info", "served 980k req/s, p99 35ms")
    b.log(19.1, "edge", "info", "tenant 4417 config v13 passed validation; published")
    first = None
    for m in [19.6, 20, 20.5, 21.5, 23, 26, 30]:
        log_id = b.log(
            m,
            "edge",
            "error",
            "worker crashed: assertion failed in path_router.resolve (edge 7.4.0) while "
            "matching tenant path rules; restarting",
        )
        first = first or log_id
    b.step_series("m-edge-err", "edge", "http_error_rate", "ratio", 0.001, 0.85, 19.6)
    b.step_series("m-edge-restarts", "edge", "worker_restarts", "count/min", 0, 2400, 19.6)
    assert first
    b.truth(C.BAD_CODE_DEPLOY, "edge", bad, first)
    return b


def xid_wraparound() -> ScenarioBuilder:
    """Mandrill, 2019-02-04: one Postgres shard of a key-value store hit transaction
    ID wraparound and stopped accepting writes; app servers spooled jobs to disk."""
    b = ScenarioBuilder(
        "x03-postgres-xid-wraparound",
        "One shard stops taking writes at transaction ID wraparound",
        Alert(
            service="mail-sender",
            summary="send queue growing; 20% of messages failing to persist. "
            "Spool disks on sender hosts are filling.",
        ),
        note=note(
            "The busiest Postgres shard (kv-shard4) fell behind on vacuum, its transaction ID "
            "age reached the wraparound limit and it refused writes. mail-sender's failed "
            "writes spooled to local disk, which then filled: a second resource running out, "
            "downstream of the first.",
            "https://mailchimp.com/what-we-learned-from-the-recent-mandrill-outage/ "
            "(Mailchimp/Mandrill, 2019-02-04)",
            "mail-sender (blaming the filling spool disk)",
            "none",
        ),
    )
    b.deploy("d-E301", "mail-sender", "12.3.0", 10, "Template rendering cache", author="omar")
    b.log(-180, "kv-shard4", "warn", 'database "kv" must be vacuumed within 11000000 transactions')
    b.log(-60, "kv-shard4", "warn", 'database "kv" must be vacuumed within 1900000 transactions')
    for m in range(0, 25, 4):
        b.log(m, "mail-sender", "info", "batch persisted: 5000 messages in 210ms")
    first = None
    for m in [25, 25.5, 26, 27.5, 30, 34]:
        log_id = b.log(
            m,
            "kv-shard4",
            "error",
            'database is not accepting commands to avoid wraparound data loss in database "kv"',
        )
        first = first or log_id
        b.log(m + 0.1, "mail-sender", "error", "write to shard4 failed; spooling job to local disk")
    b.log(36, "mail-sender", "warn", "local spool disk 91% full")
    xid = b.series(
        "m-shard4-xid-age",
        "kv-shard4",
        "datfrozenxid_age",
        "transactions",
        {m: min(2_146_483_647, 2_140_000_000 + m * 260_000) for m in range(0, 40, 2)},
    )
    b.series(
        "m-sender-spool",
        "mail-sender",
        "spool_disk_used_ratio",
        "ratio",
        {m: 0.2 if m < 25 else round(min(0.99, 0.2 + (m - 25) * 0.06), 2) for m in range(0, 40, 2)},
    )
    b.step_series("m-sender-err", "mail-sender", "persist_error_rate", "ratio", 0.0, 0.2, 25)
    assert first
    b.truth(C.RESOURCE_EXHAUSTION, "kv-shard4", first, xid)
    return b


def transit_gateway() -> ScenarioBuilder:
    """Slack, 2021-01-04: a cloud provider's transit gateway did not scale with the
    post-holiday traffic surge; packet loss saturated the web tier and broke
    autoscaling and provisioning on the way."""
    b = ScenarioBuilder(
        "x04-transit-gateway-saturation",
        "The cloud network hop between tiers drops packets under a traffic surge",
        Alert(
            service="chat-web",
            summary="message send success at 99% and falling; web tier saturated.",
        ),
        note=note(
            "The provider-managed transit-gateway between the web tier and its backends hit "
            "its packet rate limit during the first-morning-back surge and dropped packets. "
            "Backend calls timed out, web hosts went unhealthy, autoscaling churned and "
            "provision-service hit its open-files limit. The gateway is a managed dependency "
            "the team does not operate, so the category is dependency_failure.",
            "https://slack.engineering/slacks-outage-on-january-4th-2021/ (Slack, 2021-01-04)",
            "resource_exhaustion (the gateway's capacity ran out); provision-service (a "
            "visible but secondary limit)",
            "none",
        ),
    )
    b.deploy("d-E401", "chat-backend", "88.1.0", 5, "Emoji search index refresh", author="pat")
    for m in range(0, 20, 4):
        b.log(m, "chat-web", "info", "POST /api/chat.post 200 in 64ms")
    first = None
    for m in [20, 20.5, 21, 22, 24, 27]:
        log_id = b.log(
            m,
            "transit-gateway",
            "warn",
            "attachment tgw-attach-0b7 packet rate above limit; dropping packets",
        )
        first = first or log_id
        b.log(m + 0.2, "chat-web", "error", "backend call to chat-backend timed out after 5s")
    b.log(24.5, "autoscaler", "info", "scaling chat-web from 400 to 1600 instances")
    for m in [25, 26, 28]:
        b.log(m, "provision-service", "error", "accept failed: too many open files")
    b.log(26.5, "chat-web", "warn", "load balancer marked 212 hosts unhealthy")
    drops = b.step_series(
        "m-tgw-drops", "transit-gateway", "packet_drop_rate", "ratio", 0.0, 0.07, 20
    )
    b.series(
        "m-tgw-pps",
        "transit-gateway",
        "packets_per_second",
        "Mpps",
        {m: min(9.0, round(5.0 + m * 0.25, 2)) for m in range(0, 40, 2)},
    )
    b.step_series("m-web-p99", "chat-web", "http_p99_latency", "ms", 90, 5000, 20)
    b.step_series("m-web-err", "chat-web", "http_error_rate", "ratio", 0.00001, 0.01, 20)
    assert first
    b.truth(C.DEPENDENCY_FAILURE, "transit-gateway", first, drops)
    return b


def networkd_routes() -> ScenarioBuilder:
    """Datadog, 2023-03-08: an automatic OS security update restarted
    systemd-networkd on running nodes, which deleted the CNI's pod routes and took
    the nodes off the network in several regions at once."""
    b = ScenarioBuilder(
        "x05-os-update-drops-routes",
        "An automatic OS update restarts the network daemon and deletes pod routes",
        Alert(
            service="api",
            summary="API unreachable in five regions; monitors not evaluating.",
        ),
        note=note(
            "A security update to systemd arrived through an automatic OS update channel, "
            "outside the deploy pipeline, in the same window in every region. Restarting "
            "systemd-networkd on running nodes removed the routes cilium had installed, so "
            "the nodes dropped off the network. The node OS is the platform the team runs "
            "on, so the category is infrastructure.",
            "https://www.datadoghq.com/blog/2023-03-08-multiregion-infrastructure-"
            "connectivity-issue/ (Datadog, 2023-03-08)",
            "config_change or bad_code_deploy (a package update is a change, just not one in "
            "the deploy log)",
            "cilium (its routes are what disappeared and its logs report it)",
        ),
    )
    b.deploy("d-E501", "api", "41.2.0", -45, "Rate limiter: per-key burst tuning", author="sam")
    for m in range(0, 20, 4):
        b.log(m, "api", "info", "GET /v1/query 200 in 52ms")
    upgrade = b.log(
        20,
        "k8s-nodes",
        "info",
        "unattended-upgrade: installing systemd 249.11-0ubuntu3.7 (security)",
    )
    restart = b.log(20.4, "k8s-nodes", "info", "systemd-networkd.service restarted")
    first = None
    for m in [20.6, 21, 21.5, 23, 26]:
        b.log(m, "cilium", "error", "route to pod CIDR 10.4.0.0/16 missing from main table")
        b.log(m + 0.1, "k8s-nodes", "error", "node NotReady: NetworkUnavailable")
        log_id = b.log(m + 0.2, "api", "error", "upstream connect error: no route to host")
        first = first or log_id
    ready = b.step_series("m-nodes-ready", "k8s-nodes", "ready_ratio", "ratio", 1.0, 0.35, 20.6)
    b.step_series("m-api-err", "api", "http_error_rate", "ratio", 0.001, 0.9, 20.6)
    assert first
    b.truth(C.INFRASTRUCTURE, "k8s-nodes", restart, upgrade, ready, accepted=["cilium"])
    return b


def network_partition() -> ScenarioBuilder:
    """GitHub, 2018-10-21: a 43-second partition during optical equipment
    maintenance led the database orchestrator to promote primaries in the other
    coast's data center, leaving unreplicated writes and cross-country latency."""
    b = ScenarioBuilder(
        "x06-partition-cross-region-failover",
        "A 43-second link cut moves database primaries across the country",
        Alert(
            service="git-web",
            summary="pages showing stale data, webhooks delayed, write latency up tenfold.",
        ),
        note=note(
            "Maintenance on optical equipment cut the link between the east hub and the east "
            "data center for 43 seconds. The orchestrator's quorum (west plus cloud-east) "
            "promoted west primaries, so writes crossed the country and some east writes were "
            "never replicated. The postmortem's root cause is the partition; the "
            "orchestrator's cross-region promotion is what made it long. The real incident "
            "lasted 24 hours; here, minutes.",
            "https://github.blog/2018-10-30-oct21-post-incident-analysis/ (GitHub, 2018-10-21)",
            "mysql (where the stale reads show up)",
            "orchestrator (its cross-region promotion is the amplifier the follow-ups target)",
        ),
    )
    b.deploy("d-E601", "git-web", "2026.10.14", 8, "Pages build queue: batch size 50", author="lin")
    for m in range(0, 20, 4):
        b.log(m, "git-web", "info", "POST /repo/push 200 in 38ms")
    cut = b.log(20, "network", "error", "link east-hub <-> dc-east-1 down (optics maintenance)")
    b.log(20.3, "orchestrator", "warn", "dc-east-1 primaries unreachable; quorum electing")
    b.log(20.5, "orchestrator", "info", "promoted mysql-west-02 to primary for cluster repos")
    b.log(20.7, "network", "info", "link east-hub <-> dc-east-1 restored after 43s")
    first = None
    for m in [21, 22, 24, 27, 31]:
        b.log(m, "mysql", "error", "dc-east-1 writes not present on new primary; refusing failback")
        log_id = b.log(m + 0.1, "git-web", "warn", "write latency 410ms (cross-country primary)")
        first = first or log_id
    b.step_series("m-link-up", "network", "east_link_up", "bool", 1, 1, 0, (0, 40, 2))
    b.step_series("m-web-write-p99", "git-web", "write_p99_latency", "ms", 40, 410, 20.5)
    b.step_series("m-mysql-lag", "mysql", "replica_lag", "s", 0, 900, 21)
    b.step_series("m-hooks-backlog", "git-web", "webhook_backlog", "count", 0, 50000, 21)
    assert first
    b.truth(C.INFRASTRUCTURE, "network", cut, accepted=["orchestrator"])
    return b


def bgp_policy() -> ScenarioBuilder:
    """Cloudflare, 2022-06-21: a BGP export policy change reordered terms on spine
    routers so a reject came before the site-local advertisements, withdrawing
    them in 19 of the largest data centers."""
    b = ScenarioBuilder(
        "x07-bgp-policy-term-order",
        "A reordered routing policy withdraws prefixes in the busiest sites",
        Alert(
            service="edge",
            summary="requests failing in 19 data centers, about half of all traffic.",
        ),
        note=note(
            "A routing policy change meant to tidy community tags reordered its terms on the "
            "spine routers of the largest sites, putting a reject ahead of the site-local "
            "advertisements. The prefixes were withdrawn, the internal load balancer lost its "
            "spread and small clusters overloaded. Reverting the policy fixed it.",
            "https://blog.cloudflare.com/cloudflare-outage-on-june-21-2022/ (Cloudflare, "
            "2022-06-21)",
            "load-balancer (overloaded downstream of the withdrawal)",
            "none",
        ),
    )
    b.deploy(
        "d-E700",
        "load-balancer",
        "5.2.1",
        12,
        "Health check interval 5s to 3s",
        author="ravi",
        before={"HEALTH_CHECK_INTERVAL_S": "5"},
        after={"HEALTH_CHECK_INTERVAL_S": "3"},
    )
    bad = b.deploy(
        "d-E701",
        "spine-routers",
        "policy-v42",
        20,
        "Standardize site-local community tags on large sites",
        author="neteng",
        before={"BGP_EXPORT_POLICY": "v41: site-local terms, then reject"},
        after={"BGP_EXPORT_POLICY": "v42: reject term, then site-local terms"},
    )
    for m in range(0, 20, 4):
        b.log(m, "edge", "info", "served 310k req/s in site group large-19")
    b.log(20.3, "spine-routers", "warn", "withdrew 214 site-local prefixes across 19 sites")
    first = None
    for m in [20.5, 21, 22, 24, 27, 31]:
        b.log(m, "load-balancer", "error", "site-local backends unreachable; 3 clusters remain")
        log_id = b.log(m + 0.1, "edge", "error", "connect to backend pool failed; returned 503")
        first = first or log_id
    b.step_series(
        "m-spine-prefixes", "spine-routers", "advertised_prefixes", "count", 1200, 986, 20.3
    )
    b.step_series("m-edge-err", "edge", "http_error_rate", "ratio", 0.001, 0.5, 20.5)
    assert first
    b.truth(C.CONFIG_CHANGE, "spine-routers", b.config_id(bad, "BGP_EXPORT_POLICY"), bad)
    return b


def capacity_removal() -> ScenarioBuilder:
    """AWS S3, 2017-02-28: a runbook command meant to remove a few servers took a
    mistyped input and removed a large share of the index and placement
    subsystems, which then needed full restarts; everything built on the object
    store failed with it."""
    b = ScenarioBuilder(
        "x08-runbook-removes-index-capacity",
        "A mistyped runbook command removes most of the object index",
        Alert(
            service="functions",
            summary="function invocations failing at 60%; new VM launches also failing.",
        ),
        note=note(
            "An operator ran a standard capacity-removal runbook against a small billing "
            "subsystem; a mistyped input removed far more hosts, including most of "
            "object-index. With the index below its minimum, the object store could not "
            "serve reads or writes, and functions and vm-launch, which load code and images "
            "from it, failed too. The removed capacity is platform infrastructure, so the "
            "category is infrastructure.",
            "https://aws.amazon.com/message/41926/ (Amazon Web Services, 2017-02-28)",
            "config_change (an operator action); object-placement (also down, but because "
            "it depends on the index)",
            "object-store (the front end that fails every request)",
        ),
    )
    b.deploy("d-E801", "functions", "9.14.2", 10, "Runtime patch: Node 22.11", author="dee")
    for m in range(0, 20, 4):
        b.log(m, "functions", "info", "invocation ok in 120ms")
    removed = b.log(
        20,
        "object-index",
        "warn",
        "412 hosts removed from pool by runbook remove-capacity (requested target: billing)",
    )
    b.log(20.4, "object-index", "error", "below minimum capacity; restarting with integrity check")
    b.log(20.6, "object-placement", "error", "index unavailable; cannot allocate new objects")
    first = None
    for m in [20.8, 21, 22, 24, 27, 31]:
        b.log(m, "object-store", "error", "GET failed: index unavailable")
        log_id = b.log(
            m + 0.1, "functions", "error", "failed to fetch code package from object-store"
        )
        first = first or log_id
        b.log(m + 0.2, "vm-launch", "error", "image fetch from object-store failed")
    hosts = b.step_series("m-index-hosts", "object-index", "active_hosts", "count", 600, 188, 20)
    b.step_series("m-store-err", "object-store", "http_error_rate", "ratio", 0.0001, 0.95, 20.8)
    b.step_series("m-fn-err", "functions", "invocation_error_rate", "ratio", 0.001, 0.6, 20.8)
    assert first
    b.truth(C.INFRASTRUCTURE, "object-index", removed, hosts, accepted=["object-store"])
    return b


SCENARIOS = [
    waf_regex(),
    latent_bug(),
    xid_wraparound(),
    transit_gateway(),
    networkd_routes(),
    network_partition(),
    bgp_policy(),
    capacity_removal(),
]

if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent / "scenarios-external"
    for builder in SCENARIOS:
        print(builder.write(root))
