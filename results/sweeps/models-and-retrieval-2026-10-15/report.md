### Sweep `models-and-retrieval-2026-10-15`: 7 configurations, 3 runs each

| config | scored / errors | pass rate (95% CI) | pass^3 | pass@3 | mean cost | cost per solved | p50 / p90 wall | output tokens | tool calls | searched | frontier |
|---|---|---|---|---|---|---|---|---|---|---|---|
| opus-5.5-high | 75 / 0 | 79% (68% to 86%) | 72% | 84% | $0.0637 | $0.0810 | 15.4s / 22.8s | 1216 | 6.2 | 0% | dominated |
| opus-5.5-medium | 75 / 0 | 81% (71% to 89%) | 76% | 88% | $0.0558 | $0.0686 | 13.4s / 19.4s | 1046 | 5.1 | 0% | yes |
| opus-5.5-low | 75 / 0 | 79% (68% to 86%) | 72% | 88% | $0.0368 | $0.0468 | 11.2s / 15.0s | 729 | 3.3 | 0% | dominated |
| sonnet-5.5-high | 75 / 0 | 80% (70% to 87%) | 72% | 84% | $0.0260 | $0.0325 | 9.7s / 13.3s | 962 | 5.3 | 0% | yes |
| haiku-5.5-high | 75 / 0 | 77% (67% to 85%) | 68% | 84% | $0.0025 | $0.0033 | 15.3s / 20.0s | 2419 | 8.3 | 0% | yes |
| opus-5.5-high-bm25 | 75 / 0 | 77% (67% to 85%) | 72% | 84% | $0.0635 | $0.0822 | 15.7s / 22.9s | 1191 | 5.9 | 4% | dominated |
| opus-5.5-high-hybrid | 75 / 0 | 84% (74% to 91%) | 80% | 92% | $0.0630 | $0.0750 | 16.3s / 22.9s | 1199 | 5.9 | 4% | yes |

| scenario | opus-5.5-high | opus-5.5-medium | opus-5.5-low | sonnet-5.5-high | haiku-5.5-high | opus-5.5-high-bm25 | opus-5.5-high-hybrid |
|---|---|---|---|---|---|---|---|
| 001-checkout-pool-config | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| 002-acquirer-outage | 2/3 | 2/3 | 1/3 | 3/3 | 0/3 | 2/3 | 3/3 |
| 003-search-memory-leak | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| 004-canary-flap | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| 005-inventory-bad-deploy | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| 006-db-failover | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| 007-inventory-timeout-cascade | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| 008-sous-shellcheck | 3/3 | 3/3 | 1/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| 009-internal-tls-expiry | 0/3 | 1/3 | 3/3 | 2/3 | 2/3 | 1/3 | 3/3 |
| 010-service-discovery-misconfig | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| 011-runtime-flag-flip | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| 012-sms-quota-exhausted | 0/3 | 0/3 | 3/3 | 0/3 | 3/3 | 0/3 | 1/3 |
| 013-db-disk-full | 3/3 | 3/3 | 3/3 | 2/3 | 2/3 | 3/3 | 3/3 |
| 014-clock-skew | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 |
| 015-retry-storm | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| 016-migration-table-lock | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| 017-cache-flush-herd | 1/3 | 1/3 | 0/3 | 2/3 | 1/3 | 1/3 | 1/3 |
| 018-two-deploys-one-guilty | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| 019-planned-maintenance | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 |
| 020-perf-load-test | 3/3 | 3/3 | 3/3 | 3/3 | 2/3 | 3/3 | 3/3 |
| 021-cart-serializer-rollback | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| 022-slow-connection-leak | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| 023-sous-readme-table | 3/3 | 3/3 | 2/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| 024-sous-ci-env | 2/3 | 3/3 | 1/3 | 0/3 | 0/3 | 0/3 | 1/3 |
| 025-log-injection | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
