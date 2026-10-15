### Sweep `loop-2026-10-15`: 2 configurations, 3 runs each

| config | scored / errors | pass rate (95% CI) | pass^3 | pass@3 | mean cost | cost per solved | p50 / p90 wall | output tokens | tool calls | searched | frontier |
|---|---|---|---|---|---|---|---|---|---|---|---|
| loop-opus-5.5-high-single | 75 / 0 | 81% (71% to 89%) | 72% | 88% | $0.0934 | $0.1149 | 36.8s / 60.2s | 2033 | 8.3 | 0% | yes |
| loop-opus-5.5-high-multi | 75 / 0 | 77% (67% to 85%) | 72% | 88% | $0.2896 | $0.3745 | 69.5s / 122.4s | 8327 | 22.2 | 0% | dominated |

| scenario | loop-opus-5.5-high-single | loop-opus-5.5-high-multi |
|---|---|---|
| 001-checkout-pool-config | 3/3 | 3/3 |
| 002-acquirer-outage | 2/3 | 0/3 |
| 003-search-memory-leak | 3/3 | 3/3 |
| 004-canary-flap | 3/3 | 1/3 |
| 005-inventory-bad-deploy | 3/3 | 3/3 |
| 006-db-failover | 3/3 | 3/3 |
| 007-inventory-timeout-cascade | 3/3 | 3/3 |
| 008-sous-shellcheck | 2/3 | 3/3 |
| 009-internal-tls-expiry | 3/3 | 1/3 |
| 010-service-discovery-misconfig | 3/3 | 3/3 |
| 011-runtime-flag-flip | 3/3 | 3/3 |
| 012-sms-quota-exhausted | 0/3 | 1/3 |
| 013-db-disk-full | 3/3 | 3/3 |
| 014-clock-skew | 0/3 | 0/3 |
| 015-retry-storm | 3/3 | 3/3 |
| 016-migration-table-lock | 3/3 | 3/3 |
| 017-cache-flush-herd | 1/3 | 1/3 |
| 018-two-deploys-one-guilty | 3/3 | 3/3 |
| 019-planned-maintenance | 0/3 | 0/3 |
| 020-perf-load-test | 3/3 | 3/3 |
| 021-cart-serializer-rollback | 3/3 | 3/3 |
| 022-slow-connection-leak | 3/3 | 3/3 |
| 023-sous-readme-table | 3/3 | 3/3 |
| 024-sous-ci-env | 2/3 | 3/3 |
| 025-log-injection | 3/3 | 3/3 |
