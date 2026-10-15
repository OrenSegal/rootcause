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


def internal_tls_expiry() -> ScenarioBuilder:
    """The ledger's internal TLS certificate expired after renewal failed. A payments
    config deploy half an hour earlier is the only change on record."""
    b = ScenarioBuilder(
        "009-internal-tls-expiry",
        "Expired certificate on an internal endpoint",
        Alert(service="payments", summary="payments error rate at 100% on settlement calls."),
    )
    b.deploy(
        "d-9001",
        "payments",
        "3.2.0",
        -35,
        "Raise ledger client pool",
        before={"LEDGER_POOL_MAX": "20"},
        after={"LEDGER_POOL_MAX": "40"},
    )
    b.log(-60, "cert-manager", "warn", "certificate ledger-internal-tls expires in 90m; renewing")
    renew = b.log(
        10,
        "cert-manager",
        "error",
        "renewal of ledger-internal-tls failed: DNS-01 challenge record not found; giving up",
    )
    for m in range(0, 30, 4):
        b.log(m, "payments", "info", "settlement batch posted to ledger.internal in 61ms")
    first = None
    for m in [30, 30.5, 31, 33, 36, 40]:
        log_id = b.log(
            m,
            "payments",
            "error",
            "POST https://ledger.internal/v1/settle failed: x509: certificate has expired "
            "(notAfter 2026-10-14T13:30:00Z)",
        )
        first = first or log_id
    b.log(30.2, "ledger", "warn", "TLS handshake error from 10.0.4.12: remote error: bad cert")
    days = b.series(
        "m-ledger-cert-days",
        "ledger",
        "tls_cert_days_remaining",
        "days",
        {m: round(max(0.0, (30 - m) / 1440), 4) for m in range(0, 44, 4)},
    )
    b.step_series(
        "m-payments-err", "payments", "settlement_error_rate", "ratio", 0.0, 1.0, 30, (0, 44, 2)
    )
    assert first
    b.truth(C.INFRASTRUCTURE, "ledger", first, renew, days)
    return b


def service_discovery_misconfig() -> ScenarioBuilder:
    """A service-registry deploy changed the search domain, and the old DNS answers
    expired five minutes later. An orders deploy landed in between."""
    b = ScenarioBuilder(
        "010-service-discovery-misconfig",
        "Registry search-domain change breaks name resolution",
        Alert(service="orders", summary="orders error rate at 40%; calls to inventory failing."),
    )
    bad = b.deploy(
        "d-1001",
        "service-registry",
        "0.14.0",
        10,
        "Move internal zone to svc.cluster.internal",
        author="platform-bot",
        before={"DNS_SEARCH_DOMAIN": "svc.cluster.local", "RECORD_TTL_S": "300"},
        after={"DNS_SEARCH_DOMAIN": "svc.cluster.internal", "RECORD_TTL_S": "300"},
    )
    b.deploy("d-1002", "orders", "1.9.0", 13, "Add order notes field", author="ana")
    for m in range(0, 15, 3):
        b.log(m, "orders", "info", "reserved stock via inventory in 18ms")
    b.log(15.1, "service-registry", "info", "cached records for svc.cluster.local expired (300s)")
    first = None
    for m in [15.2, 15.5, 16, 17, 19, 22, 26]:
        log_id = b.log(
            m, "orders", "error", "dial tcp: lookup inventory.svc.cluster.internal: no such host"
        )
        first = first or log_id
    b.log(16, "shipping", "error", "lookup rates.svc.cluster.internal: no such host")
    b.step_series("m-orders-err", "orders", "http_error_rate", "ratio", 0.001, 0.4, 15)
    nx = b.step_series(
        "m-dns-nxdomain", "service-registry", "dns_nxdomain_rate", "per_s", 0.1, 220.0, 15
    )
    assert first
    b.truth(C.CONFIG_CHANGE, "service-registry", b.config_id(bad, "DNS_SEARCH_DOMAIN"), bad, nx)
    return b


def runtime_flag_flip() -> ScenarioBuilder:
    """A feature flag was turned on at runtime, so no deploy records it; pricing logs
    the change. A checkout deploy three minutes before the errors is the decoy."""
    b = ScenarioBuilder(
        "011-runtime-flag-flip",
        "Runtime flag flip in pricing, no deploy",
        Alert(service="checkout", summary="checkout error rate at 6%: order total mismatch."),
    )
    b.deploy("d-1101", "pricing", "2.0.3", -40, "Log currency on quote", author="raj")
    b.deploy("d-1102", "checkout", "2.4.3", 17, "Analytics event on coupon apply", author="lee")
    for m in range(0, 20, 4):
        b.log(m, "checkout", "info", "POST /checkout 200 in 150ms")
        b.log(m + 1, "pricing", "info", "quote computed in 9ms")
    flip = b.log(
        19.5,
        "pricing",
        "info",
        "runtime flag PRICING_STACKED_DISCOUNTS changed false -> true "
        "(source: flag service, actor: priya, rollout 100%)",
    )
    first = None
    for m in [20, 21, 22, 24, 27, 31]:
        log_id = b.log(
            m, "checkout", "error", "order total mismatch: cart=84.20 quote=71.57; rejecting order"
        )
        first = first or log_id
    b.log(20.5, "pricing", "warn", "discount stack depth 3 exceeds expected 1 for sku 4410")
    b.step_series("m-checkout-err", "checkout", "http_error_rate", "ratio", 0.002, 0.06, 20)
    disc = b.step_series(
        "m-pricing-discount", "pricing", "avg_discount_ratio", "ratio", 0.04, 0.19, 20
    )
    assert first
    b.truth(C.CONFIG_CHANGE, "pricing", flip, disc)
    return b


def sms_quota_exhausted() -> ScenarioBuilder:
    """The SMS provider's daily quota ran out. A marketing push raised traffic, which
    looks like the cause, and a signup deploy landed shortly before."""
    b = ScenarioBuilder(
        "012-sms-quota-exhausted",
        "Third-party SMS quota exhausted during a campaign",
        Alert(service="signup", summary="signup error rate at 35%: verification codes failing."),
    )
    b.deploy("d-1201", "signup", "5.6.0", 14, "Reword welcome screen", author="mo")
    b.log(5, "marketing", "info", "campaign autumn-push started: email to 1.2M recipients")
    for m in range(0, 18, 3):
        b.log(m, "signup", "info", "verification code sent via sms-gateway in 210ms")
    first = None
    for m in [18, 18.5, 19, 21, 24, 28, 33]:
        log_id = b.log(
            m,
            "sms-gateway",
            "error",
            "provider skyline-sms returned 429: daily message quota exceeded (250000/250000)",
        )
        first = first or log_id
        b.log(m + 0.3, "signup", "error", "send verification code failed: sms-gateway returned 503")
    b.series(
        "m-signup-rps",
        "signup",
        "http_requests",
        "per_s",
        {m: 40.0 if m < 6 else 58.0 for m in range(0, 40, 2)},
    )
    quota = b.series(
        "m-sms-quota",
        "sms-gateway",
        "provider_quota_remaining",
        "messages",
        {m: max(0.0, 30960.0 - m * 1720.0) for m in range(0, 40, 2)},
    )
    b.step_series("m-signup-err", "signup", "http_error_rate", "ratio", 0.003, 0.35, 18)
    assert first
    b.truth(C.DEPENDENCY_FAILURE, "sms-gateway", first, quota)
    return b


def db_disk_full() -> ScenarioBuilder:
    """A stuck replication slot kept WAL until the volume filled. An orders deploy
    with a new reporting query looks like it slowed the database down."""
    b = ScenarioBuilder(
        "013-db-disk-full",
        "Database volume fills with retained WAL",
        Alert(service="orders", summary="orders writes failing, error rate at 50%."),
    )
    b.deploy("d-1301", "orders", "1.9.1", 15, "Add weekly sales report query", author="ana")
    b.log(-120, "postgres", "warn", "replication slot analytics_cdc inactive; retaining WAL")
    for m in range(0, 28, 4):
        b.log(m, "orders", "info", "order written in 14ms")
    b.log(16, "orders", "info", "weekly sales report query took 2.9s")
    first = None
    for m in [28, 28.3, 29, 30, 32, 35]:
        log_id = b.log(
            m,
            "postgres",
            "error",
            'could not extend file "base/16384/24576": No space left on device',
        )
        first = first or log_id
        b.log(m + 0.2, "orders", "error", "insert failed: postgres error 53100 disk full")
    disk = b.series(
        "m-pg-disk",
        "postgres",
        "volume_used_ratio",
        "ratio",
        {m: min(1.0, round(0.86 + m * 0.005, 3)) for m in range(0, 40, 2)},
    )
    b.series(
        "m-pg-wal",
        "postgres",
        "wal_retained_bytes",
        "GiB",
        {m: round(140 + m * 1.6, 1) for m in range(0, 40, 2)},
    )
    b.step_series("m-orders-err", "orders", "http_error_rate", "ratio", 0.001, 0.5, 28)
    assert first
    b.truth(C.RESOURCE_EXHAUSTION, "postgres", first, disk)
    return b


def clock_skew() -> ScenarioBuilder:
    """One node's clock drifted after NTP lost its sources, so tokens issued there
    look like they come from the future. An auth deploy rotated signing keys earlier."""
    b = ScenarioBuilder(
        "014-clock-skew",
        "Clock skew on one node breaks token validation",
        Alert(service="api-gateway", summary="401 rate at 25% across all routes."),
    )
    b.deploy(
        "d-1401",
        "auth",
        "7.1.0",
        5,
        "Rotate JWT signing key",
        author="sec-bot",
        before={"JWT_KID": "2026-09"},
        after={"JWT_KID": "2026-10"},
    )
    b.log(6, "auth", "info", "signing with kid 2026-10; previous kid 2026-09 still accepted")
    ntp = b.log(
        12,
        "ntp",
        "error",
        "chronyd on ip-10-0-5-21: no reachable sources; clock unsynchronised, free-running",
    )
    for m in range(0, 20, 4):
        b.log(m, "api-gateway", "info", "GET /v1/orders 200 in 40ms")
    first = None
    for m in [20, 20.5, 21, 23, 26, 30]:
        log_id = b.log(
            m,
            "api-gateway",
            "error",
            "token rejected: iat is 94s in the future (issuer auth-3 on ip-10-0-5-21, kid 2026-10)",
        )
        first = first or log_id
    offset = b.series(
        "m-ntp-offset",
        "ntp",
        "clock_offset_seconds",
        "s",
        {m: 0.01 if m < 12 else round(min(94.2, (m - 12) * 11.8), 1) for m in range(0, 36, 2)},
    )
    b.step_series("m-gw-401", "api-gateway", "http_401_rate", "ratio", 0.004, 0.25, 20)
    assert first
    b.truth(C.INFRASTRUCTURE, "ntp", ntp, offset)
    return b


def retry_storm() -> ScenarioBuilder:
    """A one-minute catalog slowdown became an outage because web-bff's retry policy
    had been raised to five immediate retries. A product-api deploy is the decoy."""
    b = ScenarioBuilder(
        "015-retry-storm",
        "Retry storm amplifies a short catalog slowdown",
        Alert(service="product-api", summary="product-api saturated: p99 above 5s, errors 30%."),
    )
    bad = b.deploy(
        "d-1501",
        "web-bff",
        "8.2.0",
        -90,
        "Make product page more resilient",
        author="kim",
        before={"PRODUCT_API_RETRIES": "1", "PRODUCT_API_RETRY_BACKOFF_MS": "200"},
        after={"PRODUCT_API_RETRIES": "5", "PRODUCT_API_RETRY_BACKOFF_MS": "0"},
    )
    b.deploy("d-1502", "product-api", "3.3.1", 18, "Add size chart to response", author="lee")
    for m in range(0, 20, 4):
        b.log(m, "product-api", "info", "GET /products/{id} 200 in 35ms")
    b.log(20, "catalog", "warn", "vacuum on products table: queries slow for ~60s")
    b.log(21.2, "catalog", "info", "vacuum finished; query latency back to normal")
    first = None
    for m in [20.5, 21, 22, 24, 27, 31]:
        log_id = b.log(
            m, "product-api", "error", "rejecting request: worker pool full (256/256), queue 4100"
        )
        first = first or log_id
        b.log(m + 0.1, "web-bff", "warn", "product-api 503, retrying immediately (attempt 5/5)")
    rps = b.series(
        "m-product-rps",
        "product-api",
        "http_requests",
        "per_s",
        {m: 900.0 if m < 20 else 5400.0 for m in range(0, 36, 2)},
    )
    b.series(
        "m-catalog-p99",
        "catalog",
        "query_p99_latency",
        "ms",
        {m: 1800.0 if m == 20 else 12.0 for m in range(0, 36, 2)},
    )
    b.step_series("m-product-err", "product-api", "http_error_rate", "ratio", 0.001, 0.3, 21)
    assert first
    b.truth(C.CONFIG_CHANGE, "web-bff", b.config_id(bad, "PRODUCT_API_RETRIES"), bad, rps)
    return b


def migration_table_lock() -> ScenarioBuilder:
    """A migration ships in the same deploy as a harmless log-level change and locks
    the orders table. The config diff points at the wrong half of the deploy."""
    b = ScenarioBuilder(
        "016-migration-table-lock",
        "Migration takes a table lock inside a deploy with a config change",
        Alert(service="orders", summary="orders write latency above 30s; error rate at 60%."),
    )
    bad = b.deploy(
        "d-1601",
        "orders",
        "2.0.0",
        20,
        "Add fulfillment_status column; quieter logs",
        before={"LOG_LEVEL": "debug"},
        after={"LOG_LEVEL": "info"},
    )
    for m in range(0, 20, 4):
        b.log(m, "orders", "info", "order written in 13ms")
    mig = b.log(
        20.2,
        "orders",
        "info",
        "running migration 0042: ALTER TABLE orders ADD COLUMN fulfillment_status "
        "text NOT NULL DEFAULT 'new' (rewrites 41M rows)",
    )
    lock = b.log(
        20.5,
        "postgres",
        "warn",
        "process 8812 holds AccessExclusiveLock on relation orders; 212 waiters",
    )
    for m in [21, 21.5, 22, 24, 27]:
        b.log(m, "orders", "error", "insert into orders timed out after 30000ms waiting for lock")
    b.step_series("m-orders-err", "orders", "http_error_rate", "ratio", 0.001, 0.6, 21)
    b.step_series("m-pg-lock-waits", "postgres", "lock_waiters", "count", 0, 212, 21)
    b.truth(C.BAD_CODE_DEPLOY, "orders", bad, mig, lock)
    return b


def cache_flush_herd() -> ScenarioBuilder:
    """An operator flushed the product cache from a runbook; every miss went to the
    database at once and used up its connections. No deploy is involved; a catalog
    deploy shortly before is the decoy. The exhausted resource is the database."""
    b = ScenarioBuilder(
        "017-cache-flush-herd",
        "Thundering herd on the database after a cache flush",
        Alert(service="product-api", summary="product-api error rate at 20%, p99 above 3s."),
    )
    b.deploy("d-1701", "catalog", "6.0.4", 16, "Index on products.brand_id", author="ana")
    for m in range(0, 20, 4):
        b.log(m, "product-api", "info", "GET /products/{id} 200 in 21ms (cache hit)")
    flush = b.log(
        19.8,
        "ops",
        "info",
        "runbook clear-stale-prices: FLUSHDB on redis product-cache issued by oncall dee",
    )
    first = None
    for m in [20, 20.3, 21, 22, 24, 26]:
        log_id = b.log(
            m,
            "postgres",
            "error",
            "FATAL: remaining connection slots are reserved for superuser (500/500 in use)",
        )
        first = first or log_id
        b.log(m + 0.1, "product-api", "error", "query products failed: too many clients")
    b.series(
        "m-cache-hit",
        "redis",
        "cache_hit_ratio",
        "ratio",
        {
            m: 0.97 if m < 20 else min(0.97, round(0.02 + (m - 20) * 0.06, 2))
            for m in range(0, 40, 2)
        },
    )
    conns = b.step_series(
        "m-pg-conns", "postgres", "active_connections", "connections", 120, 500, 20
    )
    b.step_series("m-product-err", "product-api", "http_error_rate", "ratio", 0.001, 0.2, 20)
    assert first
    b.truth(C.RESOURCE_EXHAUSTION, "postgres", first, conns, flush)
    return b


def two_deploys_one_guilty() -> ScenarioBuilder:
    """Payments and shipping deployed within the same minute. Shipping's landed last;
    payments' broke tax calculation."""
    b = ScenarioBuilder(
        "018-two-deploys-one-guilty",
        "Two deploys in the same minute, one guilty",
        Alert(service="checkout", summary="checkout error rate at 14%."),
    )
    bad = b.deploy("d-1801", "payments", "3.3.0", 21.1, "Tax engine v2 for EU orders")
    b.deploy("d-1802", "shipping", "2.7.0", 21.6, "New carrier logos", author="mo")
    for m in range(0, 22, 4):
        b.log(m, "checkout", "info", "POST /checkout 200 in 160ms")
    first = None
    for m in [22, 22.5, 23, 25, 28, 32]:
        log_id = b.log(
            m,
            "payments",
            "error",
            "TypeError: cannot read 'rate' of undefined in tax/v2/eu.ts:88 (country=PT)",
        )
        first = first or log_id
        b.log(m + 0.2, "checkout", "error", "payment intent failed: payments returned 500")
    b.log(22.4, "shipping", "info", "carrier logo cache warmed")
    b.step_series("m-checkout-err", "checkout", "http_error_rate", "ratio", 0.002, 0.14, 22)
    b.step_series("m-payments-err", "payments", "http_error_rate", "ratio", 0.001, 0.16, 22)
    b.step_series("m-shipping-err", "shipping", "http_error_rate", "ratio", 0.001, 0.001, 22)
    assert first
    b.truth(C.BAD_CODE_DEPLOY, "payments", bad, first)
    return b


def planned_maintenance() -> ScenarioBuilder:
    """Reporting errors inside an announced warehouse maintenance window. Nothing is
    broken; a reporting deploy shortly before looks like a cause."""
    b = ScenarioBuilder(
        "019-planned-maintenance",
        "Errors inside an announced maintenance window",
        Alert(service="reporting", summary="reporting error rate at 70% on dashboard queries."),
    )
    b.deploy("d-1901", "reporting", "1.12.0", 14, "New revenue chart", author="raj")
    window = b.log(
        -1440,
        "change-calendar",
        "info",
        "CHG-3381 approved: warehouse-db storage upgrade 13:20-13:40 UTC; "
        "reporting dashboards unavailable; owner: dba-team",
    )
    b.log(19.9, "warehouse-db", "info", "entering maintenance mode for CHG-3381; rejecting queries")
    for m in range(0, 20, 4):
        b.log(m, "reporting", "info", "dashboard query served in 820ms")
    for m in [20, 21, 23, 26, 30, 35]:
        b.log(m, "reporting", "error", "warehouse-db unavailable: server is in maintenance mode")
    done = b.log(40, "warehouse-db", "info", "maintenance CHG-3381 complete; accepting queries")
    b.series(
        "m-reporting-err",
        "reporting",
        "http_error_rate",
        "ratio",
        {m: 0.7 if 20 <= m < 40 else 0.002 for m in range(0, 48, 2)},
    )
    b.truth(C.NO_INCIDENT, "reporting", window, done)
    return b


def perf_load_test() -> ScenarioBuilder:
    """The perf team's load test against production paged on latency. Real users are
    unaffected; a checkout deploy two minutes before is the decoy."""
    b = ScenarioBuilder(
        "020-perf-load-test",
        "Latency page from a scheduled load test",
        Alert(service="checkout", summary="p99 latency above 2.5s for 8 minutes."),
    )
    b.deploy("d-2001", "checkout", "2.4.4", 18, "Tidy cart page markup", author="lee")
    start = b.log(
        20,
        "perf-runner",
        "info",
        "k6 run perf-2026-10-14 started: 4000 VUs against checkout, header x-synthetic=perf",
    )
    for m in range(0, 36, 3):
        b.log(m, "checkout", "info", "POST /checkout 200 in 150ms (organic)")
    for m in [21, 23, 25, 27]:
        b.log(m, "checkout", "warn", "slow request 2700ms (x-synthetic=perf)")
    b.log(28.5, "perf-runner", "info", "k6 run perf-2026-10-14 finished: p99 2.71s")
    b.series(
        "m-checkout-p99",
        "checkout",
        "http_p99_latency",
        "ms",
        {m: 2700.0 if 20 <= m < 29 else 170.0 for m in range(0, 40, 2)},
    )
    organic = b.step_series(
        "m-checkout-p99-organic", "checkout", "http_p99_latency_organic", "ms", 170, 172, 20
    )
    b.step_series(
        "m-checkout-err", "checkout", "http_error_rate", "ratio", 0.002, 0.002, 0, (0, 40, 2)
    )
    b.truth(C.NO_INCIDENT, "checkout", start, organic)
    return b


def cart_serializer_rollback() -> ScenarioBuilder:
    """A cart deploy changed the session format; errors began when old sessions
    expired from cache, after an unrelated deploy. The right action is a rollback,
    and the operator approves it."""
    b = ScenarioBuilder(
        "021-cart-serializer-rollback",
        "Cart deploy breaks sessions once the cache turns over",
        Alert(service="cart", summary="cart error rate at 45%: add-to-cart failing."),
        approval="approve",
    )
    bad = b.deploy("d-2101", "cart", "5.0.0", 10, "Switch session codec to msgpack", author="kim")
    b.deploy("d-2102", "search", "3.0.3", 14, "Synonyms for color names", author="lee")
    b.log(10.5, "cart", "info", "starting cart 5.0.0 (session codec=msgpack, cache ttl 300s)")
    for m in range(0, 15, 3):
        b.log(m, "cart", "info", "POST /cart/items 200 in 30ms")
    first = None
    for m in [15.6, 16, 17, 19, 22, 26]:
        log_id = b.log(
            m,
            "cart",
            "error",
            "SessionDecodeError: msgpack: invalid code 0x7b (session written by cart 4.x as json)",
        )
        first = first or log_id
    b.step_series("m-cart-err", "cart", "http_error_rate", "ratio", 0.001, 0.45, 16)
    assert first
    b.truth(C.BAD_CODE_DEPLOY, "cart", bad, first)
    return b


def slow_connection_leak() -> ScenarioBuilder:
    """A notifications deploy three hours ago leaks a database connection on every
    failed send. The pool ran dry just now; a config deploy five minutes earlier is
    the obvious suspect."""
    b = ScenarioBuilder(
        "022-slow-connection-leak",
        "Connection leak from a deploy three hours earlier",
        Alert(service="notifications", summary="notifications error rate at 90%."),
    )
    bad = b.deploy(
        "d-2201", "notifications", "4.0.0", -180, "Batch push sends; new retry wrapper", "mo"
    )
    b.deploy(
        "d-2202",
        "notifications",
        "4.0.1",
        25,
        "Lower push batch size",
        before={"PUSH_BATCH": "500"},
        after={"PUSH_BATCH": "250"},
    )
    leak = b.log(
        -60,
        "notifications",
        "warn",
        "connection held for 3600s without release (acquired in retry_wrapper.py:41)",
    )
    for m in range(0, 30, 5):
        b.log(m, "notifications", "warn", "push send failed for 3 devices, will retry next batch")
    first = None
    for m in [30, 30.5, 31, 33, 36]:
        log_id = b.log(
            m,
            "notifications",
            "error",
            "timeout acquiring DB connection: pool exhausted (in_use=100/100, idle=0)",
        )
        first = first or log_id
    pool = b.series(
        "m-notif-pool",
        "notifications",
        "db_pool_in_use",
        "connections",
        {m: min(100.0, round(64 + (m + 180) * 0.172, 1)) for m in range(-180, 40, 10)},
    )
    b.step_series("m-notif-err", "notifications", "http_error_rate", "ratio", 0.002, 0.9, 30)
    assert first
    b.truth(C.BAD_CODE_DEPLOY, "notifications", bad, leak, pool)
    return b


SCENARIOS = [
    checkout_pool_config(),
    acquirer_outage(),
    search_memory_leak(),
    canary_flap(),
    inventory_bad_deploy(),
    db_failover(),
    inventory_timeout_cascade(),
    internal_tls_expiry(),
    service_discovery_misconfig(),
    runtime_flag_flip(),
    sms_quota_exhausted(),
    db_disk_full(),
    clock_skew(),
    retry_storm(),
    migration_table_lock(),
    cache_flush_herd(),
    two_deploys_one_guilty(),
    planned_maintenance(),
    perf_load_test(),
    cart_serializer_rollback(),
    slow_connection_leak(),
]

if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent / "scenarios"
    for builder in SCENARIOS:
        print(builder.write(root))
