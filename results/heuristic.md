### rootcause eval: `heuristic` (single)

Pass rate **12%** over 24 scenario(s), total cost $0.0000.

| scenario | result | category | service | evidence | steps | tools | cost |
|---|---|---|---|---|---|---|---|
| 001-checkout-pool-config | pass | ok | ok | ok | 3 | 3 | $0.0000 |
| 002-acquirer-outage | fail (answered) | wrong | wrong | missing | 3 | 3 | $0.0000 |
| 003-search-memory-leak | fail (answered) | wrong | wrong | ok | 3 | 3 | $0.0000 |
| 004-canary-flap | fail (answered) | wrong | ok | missing | 3 | 3 | $0.0000 |
| 005-inventory-bad-deploy | pass | ok | ok | ok | 3 | 3 | $0.0000 |
| 006-db-failover | fail (answered) | wrong | wrong | missing | 3 | 3 | $0.0000 |
| 007-inventory-timeout-cascade | fail (answered) | wrong | wrong | missing | 3 | 3 | $0.0000 |
| 008-sous-shellcheck | fail (answered) | wrong | ok | ok | 3 | 3 | $0.0000 |
| 009-internal-tls-expiry | fail (answered) | wrong | wrong | ok | 3 | 3 | $0.0000 |
| 010-service-discovery-misconfig | fail (answered) | wrong | wrong | missing | 3 | 3 | $0.0000 |
| 011-runtime-flag-flip | fail (answered) | wrong | wrong | missing | 3 | 3 | $0.0000 |
| 012-sms-quota-exhausted | fail (answered) | wrong | wrong | missing | 3 | 3 | $0.0000 |
| 013-db-disk-full | fail (answered) | wrong | wrong | missing | 3 | 3 | $0.0000 |
| 014-clock-skew | fail (answered) | wrong | wrong | missing | 3 | 3 | $0.0000 |
| 015-retry-storm | fail (answered) | wrong | wrong | missing | 3 | 3 | $0.0000 |
| 016-migration-table-lock | fail (answered) | wrong | ok | ok | 3 | 3 | $0.0000 |
| 017-cache-flush-herd | fail (answered) | wrong | wrong | missing | 3 | 3 | $0.0000 |
| 018-two-deploys-one-guilty | fail (answered) | ok | wrong | missing | 3 | 3 | $0.0000 |
| 019-planned-maintenance | fail (answered) | wrong | ok | missing | 3 | 3 | $0.0000 |
| 020-perf-load-test | fail (answered) | ok | ok | missing | 3 | 3 | $0.0000 |
| 021-cart-serializer-rollback | fail (answered) | ok | wrong | ok | 3 | 3 | $0.0000 |
| 022-slow-connection-leak | fail (answered) | wrong | ok | missing | 3 | 3 | $0.0000 |
| 023-sous-readme-table | pass | ok | ok | ok | 3 | 3 | $0.0000 |
| 024-sous-ci-env | fail (answered) | wrong | ok | missing | 3 | 3 | $0.0000 |
