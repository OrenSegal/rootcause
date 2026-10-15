### Sweep `langgraph-2026-10-15`: 2 configurations, 3 runs each

| config | scored / errors | pass rate (95% CI) | pass^3 | pass@3 | mean cost | cost per solved | p50 / p90 wall | output tokens | tool calls | searched | frontier |
|---|---|---|---|---|---|---|---|---|---|---|---|
| langgraph-opus-5.5-high-single | 75 / 0 | 84% (74% to 91%) | 84% | 84% | $0.0936 | $0.1114 | 34.9s / 48.7s | 2056 | 8.4 | 0% | yes |
| langgraph-opus-5.5-high-multi | 75 / 0 | 81% (71% to 89%) | 72% | 92% | $0.2789 | $0.3430 | 69.0s / 120.7s | 8250 | 20.0 | 0% | dominated |

| scenario | langgraph-opus-5.5-high-single | langgraph-opus-5.5-high-multi |
|---|---|---|
| 001-checkout-pool-config | 3/3 | 3/3 |
| 002-acquirer-outage | 3/3 | 1/3 |
| 003-search-memory-leak | 3/3 | 3/3 |
| 004-canary-flap | 3/3 | 2/3 |
| 005-inventory-bad-deploy | 3/3 | 3/3 |
| 006-db-failover | 3/3 | 3/3 |
| 007-inventory-timeout-cascade | 3/3 | 3/3 |
| 008-sous-shellcheck | 3/3 | 3/3 |
| 009-internal-tls-expiry | 3/3 | 3/3 |
| 010-service-discovery-misconfig | 3/3 | 3/3 |
| 011-runtime-flag-flip | 3/3 | 3/3 |
| 012-sms-quota-exhausted | 0/3 | 1/3 |
| 013-db-disk-full | 3/3 | 3/3 |
| 014-clock-skew | 0/3 | 0/3 |
| 015-retry-storm | 3/3 | 3/3 |
| 016-migration-table-lock | 3/3 | 3/3 |
| 017-cache-flush-herd | 0/3 | 2/3 |
| 018-two-deploys-one-guilty | 3/3 | 3/3 |
| 019-planned-maintenance | 0/3 | 0/3 |
| 020-perf-load-test | 3/3 | 3/3 |
| 021-cart-serializer-rollback | 3/3 | 3/3 |
| 022-slow-connection-leak | 3/3 | 3/3 |
| 023-sous-readme-table | 3/3 | 3/3 |
| 024-sous-ci-env | 3/3 | 1/3 |
| 025-log-injection | 3/3 | 3/3 |
