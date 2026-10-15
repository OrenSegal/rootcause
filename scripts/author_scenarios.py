"""Every authored scenario, as code. Regenerate the fixtures with:

    uv run python scripts/author_scenarios.py

tests/test_scenarios.py fails if the committed fixtures differ from this file.

Each scenario is built around a mistake a responder could make. The baseline
heuristic ("blame the last deploy before the first error") is right on 001 and
005 and wrong on the rest, by design.
"""

from pathlib import Path

from rootcause.adapters.scenario_builder import ScenarioBuilder
from rootcause.domain.scenario import Alert, Category

C = Category


def checkout_pool_config() -> ScenarioBuilder:
    """A deploy cut the DB pool from 50 to 5. Decoys: a UI flag in the same deploy,
    a payments deploy an hour earlier, a search deploy after onset."""
    b = ScenarioBuilder(
        "001-checkout-pool-config",
        "Checkout deploy shrinks the DB connection pool",
        Alert(
            service="checkout",
            summary="p99 latency above 2s for 10 minutes and error rate at 8%. "
            "Payments is also paging on elevated upstream errors.",
        ),
    )
    b.deploy(
        "d-1041",
        "payments",
        "1.9.0",
        -50,
        "Retry idempotency keys on card capture",
        author="maya",
        before={"CAPTURE_RETRIES": "2"},
        after={"CAPTURE_RETRIES": "3"},
    )
    bad = b.deploy(
        "d-1042",
        "checkout",
        "2.4.1",
        20,
        "Enable new cart UI; tidy DB settings",
        before={"DB_POOL_MAX": "50", "FEATURE_NEW_CART_UI": "false"},
        after={"DB_POOL_MAX": "5", "FEATURE_NEW_CART_UI": "true"},
    )
    b.deploy("d-1043", "search", "3.0.2", 35, "Bump ranking model", author="lee")
    for m in range(0, 20, 4):
        b.log(m, "checkout", "info", "POST /checkout 200 in 142ms")
        b.log(m + 1, "payments", "info", "capture ok in 88ms")
    b.log(20.5, "checkout", "info", "starting checkout-api 2.4.1 (pool max=5)")
    b.log(21, "checkout", "info", "feature flag FEATURE_NEW_CART_UI=true")
    for i, m in enumerate([22, 23, 24, 26, 28, 31, 34, 38, 42, 47]):
        b.log(
            m,
            "checkout",
            "error",
            "timeout acquiring DB connection from pool after 2000ms "
            f"(max=5, in_use=5, waiting={12 + i * 4})",
            trace_id=f"t-{900 + i}",
        )
        b.log(m + 0.5, "payments", "warn", "upstream checkout returned 504, will retry")
    b.log(25, "checkout", "warn", "request queue depth 140 exceeds threshold 50")
    b.log(40, "payments", "error", "capture aborted: checkout session expired")
    b.step_series("m-checkout-p99", "checkout", "http_p99_latency", "ms", 180, 2400, 22)
    b.step_series("m-checkout-err", "checkout", "http_error_rate", "ratio", 0.002, 0.081, 22)
    b.step_series("m-checkout-pool", "checkout", "db_pool_in_use", "connections", 14, 5, 22)
    b.step_series("m-payments-err", "payments", "http_error_rate", "ratio", 0.001, 0.034, 22)
    b.step_series("m-db-conn", "postgres", "active_connections", "connections", 62, 17, 22)
    b.span("s-1", "t-900", "checkout", "POST /checkout", 22, 2214, "error")
    b.span("s-2", "t-900", "checkout", "db.pool.acquire", 22, 2001, "error", parent_id="s-1")
    b.span("s-3", "t-100", "checkout", "POST /checkout", 10, 141)
    b.span("s-4", "t-100", "checkout", "db.pool.acquire", 10, 1, parent_id="s-3")
    b.truth(C.CONFIG_CHANGE, "checkout", b.config_id(bad, "DB_POOL_MAX"), bad)
    return b


def acquirer_outage() -> ScenarioBuilder:
    """The card acquirer is down. A harmless payments deploy landed six minutes earlier."""
    b = ScenarioBuilder(
        "002-acquirer-outage",
        "Card acquirer outage behind a fresh payments deploy",
        Alert(service="payments", summary="payment error rate at 31% for 5 minutes."),
    )
    b.deploy("d-2001", "payments", "3.1.0", 15, "Refactor receipt email templates", author="ana")
    for m in range(0, 21, 3):
        b.log(m, "payments", "info", "capture ok in 91ms")
        b.log(m + 1, "card-gateway", "info", "authorize ok via northwind-pay in 64ms")
    b.log(16, "payments", "info", "starting payments 3.1.0")
    first = None
    for m in [21, 22, 23, 25, 27, 30, 34, 39]:
        log_id = b.log(
            m,
            "card-gateway",
            "error",
            "upstream acquirer northwind-pay returned 503 Service Unavailable",
        )
        first = first or log_id
        b.log(m + 0.5, "payments", "error", "card-gateway responded 502 to authorize")
    b.log(24, "card-gateway", "warn", "northwind-pay status page: partial outage, card auth")
    b.step_series("m-payments-err", "payments", "http_error_rate", "ratio", 0.002, 0.31, 21)
    b.step_series("m-payments-p99", "payments", "http_p99_latency", "ms", 210, 260, 21)
    gw = b.step_series(
        "m-cardgw-upstream-err", "card-gateway", "upstream_error_rate", "ratio", 0.001, 0.62, 21
    )
    assert first
    b.truth(C.DEPENDENCY_FAILURE, "card-gateway", first, gw)
    return b


def search_memory_leak() -> ScenarioBuilder:
    """Search leaks memory until the container is killed. No deploy is anywhere near."""
    b = ScenarioBuilder(
        "003-search-memory-leak",
        "Search pods OOM-killed after a slow leak",
        Alert(service="search", summary="search error rate at 12% and pods restarting."),
    )
    b.deploy("d-3001", "checkout", "2.3.9", -140, "Copy changes on receipt page", author="lee")
    b.deploy(
        "d-3002",
        "inventory",
        "4.1.7",
        -95,
        "Tune cache TTL",
        before={"CACHE_TTL_S": "60"},
        after={"CACHE_TTL_S": "120"},
    )
    for m in range(0, 20, 5):
        b.log(m, "search", "info", "query served in 38ms")
    b.log(12, "search", "warn", "heap usage 91% of limit 4Gi on search-7")
    b.log(18, "search", "warn", "GC pause 1.8s on search-7")
    oom = b.log(
        22, "search", "error", "OOMKilled: container search-7 exceeded memory limit 4Gi, restarting"
    )
    for m in [23, 24, 26, 31, 33]:
        b.log(m, "search", "error", "upstream connect error: search-7 not ready")
    b.log(30, "search", "error", "OOMKilled: container search-7 exceeded memory limit 4Gi")
    mem = b.series(
        "m-search-mem",
        "search",
        "memory_working_set",
        "GiB",
        {m: round(2.1 + m * 0.085, 3) if m < 22 else 1.2 + (m - 22) * 0.2 for m in range(0, 40, 2)},
    )
    b.step_series("m-search-err", "search", "http_error_rate", "ratio", 0.001, 0.12, 22)
    b.step_series("m-search-restarts", "search", "container_restarts", "count", 0, 2, 22)
    b.truth(C.RESOURCE_EXHAUSTION, "search", oom, mem)
    return b


def canary_flap() -> ScenarioBuilder:
    """A cold synthetic probe tripped a latency alert. Real traffic is fine, and a
    config deploy earlier plus one benign client error invite a wrong blame."""
    b = ScenarioBuilder(
        "004-canary-flap",
        "Latency alert from a cold synthetic probe",
        Alert(service="checkout", summary="p99 latency above 2s (single evaluation window)."),
    )
    b.deploy(
        "d-4001",
        "checkout",
        "2.4.0",
        10,
        "Raise log verbosity for the cart team",
        before={"LOG_LEVEL": "info"},
        after={"LOG_LEVEL": "debug"},
    )
    for m in range(0, 40, 3):
        b.log(m, "checkout", "info", "POST /checkout 200 in 139ms")
    canary = b.log(
        24,
        "synthetic-canary",
        "warn",
        "probe checkout/ap-south slow: 2310ms (cold start, first run after deploy of probe runner)",
    )
    b.log(23, "checkout", "error", "client disconnected before response (499)")
    b.series(
        "m-checkout-p99",
        "checkout",
        "http_p99_latency",
        "ms",
        {m: 2310.0 if m == 24 else 175.0 for m in range(0, 40, 2)},
    )
    err = b.step_series(
        "m-checkout-err", "checkout", "http_error_rate", "ratio", 0.002, 0.002, 0, span=(0, 40, 2)
    )
    b.truth(C.NO_INCIDENT, "checkout", canary, err)
    return b


def inventory_bad_deploy() -> ScenarioBuilder:
    """A code deploy introduced an exception. The baseline gets this one right."""
    b = ScenarioBuilder(
        "005-inventory-bad-deploy",
        "Inventory deploy ships a crash in the reservation path",
        Alert(service="inventory", summary="inventory error rate at 18%."),
        approval="approve",
    )
    bad = b.deploy("d-5001", "inventory", "4.2.0", 18, "New reservation path behind no flag")
    for m in range(0, 18, 4):
        b.log(m, "inventory", "info", "reserve ok in 22ms")
    first = None
    for m in [19, 20, 22, 25, 29]:
        log_id = b.log(
            m,
            "inventory",
            "error",
            "NullReferenceError in ReservationService.reserve (reservation.go:212)",
        )
        first = first or log_id
    b.step_series("m-inventory-err", "inventory", "http_error_rate", "ratio", 0.001, 0.18, 19)
    assert first
    b.truth(C.BAD_CODE_DEPLOY, "inventory", bad, first)
    return b


def db_failover() -> ScenarioBuilder:
    """The database primary failed over. An orders config deploy two minutes earlier
    looks guilty and isn't."""
    b = ScenarioBuilder(
        "006-db-failover",
        "Postgres primary failover during an orders deploy",
        Alert(service="orders", summary="orders error rate at 22%; checkout also degraded."),
    )
    b.deploy(
        "d-6001",
        "orders",
        "1.8.0",
        20,
        "Larger write batches",
        before={"ORDERS_BATCH_SIZE": "100"},
        after={"ORDERS_BATCH_SIZE": "200"},
    )
    for m in range(0, 22, 4):
        b.log(m, "orders", "info", "order written in 12ms")
    failover = b.log(
        21.8,
        "postgres",
        "warn",
        "primary pg-1 lost quorum on node ip-10-0-3-17; promoting replica pg-2",
    )
    for m in [22, 22.5, 23, 23.5, 24]:
        b.log(m, "orders", "error", "could not connect to server: connection refused (pg-1:5432)")
        b.log(m + 0.2, "checkout", "error", "order create failed: orders returned 500")
    b.log(25, "postgres", "info", "pg-2 accepting writes as primary")
    b.log(25.5, "orders", "info", "reconnected to primary pg-2")
    up = b.series(
        "m-pg-primary-up",
        "postgres",
        "primary_available",
        "bool",
        {m: 0.0 if 22 <= m < 26 else 1.0 for m in range(0, 40, 2)},
    )
    b.series(
        "m-orders-err",
        "orders",
        "http_error_rate",
        "ratio",
        {m: 0.22 if 22 <= m < 26 else 0.002 for m in range(0, 40, 2)},
    )
    b.truth(C.INFRASTRUCTURE, "postgres", failover, up)
    return b


def inventory_timeout_cascade() -> ScenarioBuilder:
    """Inventory's DB timeout was cut tenfold; checkout pages. A checkout deploy
    landed between the cause and the symptom."""
    b = ScenarioBuilder(
        "007-inventory-timeout-cascade",
        "Inventory timeout change cascades into checkout",
        Alert(service="checkout", summary="checkout error rate at 9%."),
    )
    bad = b.deploy(
        "d-7001",
        "inventory",
        "4.3.0",
        12,
        "Fail fast on slow queries",
        before={"INVENTORY_DB_TIMEOUT_MS": "3000"},
        after={"INVENTORY_DB_TIMEOUT_MS": "300"},
    )
    b.deploy("d-7002", "checkout", "2.4.2", 13.5, "Copy tweak on cart page", author="lee")
    for m in range(0, 12, 3):
        b.log(m, "inventory", "info", "reserve ok in 410ms")
        b.log(m + 1, "checkout", "info", "POST /checkout 200 in 520ms")
    for m in [13, 13.5, 15, 17, 20, 24]:
        b.log(m, "inventory", "error", "query canceled: statement timeout after 300ms (reserve)")
    for m in [14, 16, 18, 21, 25]:
        b.log(m, "checkout", "error", "inventory reserve failed: inventory returned 500")
    b.step_series("m-inventory-err", "inventory", "http_error_rate", "ratio", 0.001, 0.27, 13)
    b.step_series("m-checkout-err", "checkout", "http_error_rate", "ratio", 0.002, 0.09, 14)
    b.truth(C.CONFIG_CHANGE, "inventory", b.config_id(bad, "INVENTORY_DB_TIMEOUT_MS"), bad)
    return b


SCENARIOS = [
    checkout_pool_config(),
    acquirer_outage(),
    search_memory_leak(),
    canary_flap(),
    inventory_bad_deploy(),
    db_failover(),
    inventory_timeout_cascade(),
]

if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent / "scenarios"
    for builder in SCENARIOS:
        print(builder.write(root))
