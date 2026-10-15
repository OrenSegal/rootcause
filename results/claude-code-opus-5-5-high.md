### rootcause eval: `claude-code:claude-opus-5-5:high` (single)

Pass rate **75%** over 24 scenario(s), total cost $1.5974.

| scenario | result | category | service | evidence | steps | tools | cost |
|---|---|---|---|---|---|---|---|
| 001-checkout-pool-config | pass | ok | ok | ok | 4 | 7 | $0.0819 |
| 002-acquirer-outage | fail (answered) | ok | wrong | ok | 3 | 7 | $0.0763 |
| 003-search-memory-leak | pass | ok | ok | ok | 4 | 6 | $0.0694 |
| 004-canary-flap | pass | ok | ok | ok | 3 | 5 | $0.0446 |
| 005-inventory-bad-deploy | pass | ok | ok | ok | 4 | 7 | $0.0516 |
| 006-db-failover | pass | ok | ok | ok | 3 | 4 | $0.0458 |
| 007-inventory-timeout-cascade | pass | ok | ok | ok | 4 | 8 | $0.0659 |
| 008-sous-shellcheck | pass | ok | ok | ok | 3 | 3 | $0.0376 |
| 009-internal-tls-expiry | fail (answered) | ok | wrong | ok | 4 | 7 | $0.0548 |
| 010-service-discovery-misconfig | pass | ok | ok | ok | 4 | 7 | $0.0648 |
| 011-runtime-flag-flip | pass | ok | ok | ok | 3 | 7 | $0.0696 |
| 012-sms-quota-exhausted | fail (answered) | wrong | ok | ok | 3 | 6 | $0.0706 |
| 013-db-disk-full | pass | ok | ok | ok | 4 | 7 | $0.0765 |
| 014-clock-skew | fail (answered) | ok | wrong | ok | 4 | 7 | $0.0623 |
| 015-retry-storm | pass | ok | ok | ok | 5 | 10 | $0.0858 |
| 016-migration-table-lock | pass | ok | ok | ok | 3 | 6 | $0.0561 |
| 017-cache-flush-herd | pass | ok | ok | ok | 5 | 10 | $0.0966 |
| 018-two-deploys-one-guilty | pass | ok | ok | ok | 4 | 7 | $0.0614 |
| 019-planned-maintenance | fail (answered) | ok | wrong | ok | 4 | 6 | $0.0560 |
| 020-perf-load-test | pass | ok | ok | ok | 3 | 5 | $0.0568 |
| 021-cart-serializer-rollback | fail (answered) | ok | ok | invented | 4 | 5 | $0.0630 |
| 022-slow-connection-leak | pass | ok | ok | ok | 4 | 7 | $0.0681 |
| 023-sous-readme-table | pass | ok | ok | ok | 3 | 3 | $0.0372 |
| 024-sous-ci-env | pass | ok | ok | ok | 5 | 8 | $0.1447 |
