### Sweep `external-heldout-2026-11-07`: 2 configurations, 3 runs each (accepted-service grade)

| config | scored / errors | pass rate (95% CI) | pass^3 | pass@3 | mean cost | cost per solved | p50 / p90 wall | output tokens | tool calls | searched | frontier |
|---|---|---|---|---|---|---|---|---|---|---|---|
| heuristic | 24 / 0 | 25% (0% to 62%) | 25% | 25% | $0.0000 | $0.0000 | 0.1s / 0.1s | 0 | 3.0 | 0% | yes |
| haiku-5.5-high | 24 / 0 | 50% (21% to 79%) | 38% | 62% | $0.0030 | $0.0059 | 13.2s / 18.7s | 2498 | 9.2 | 0% | yes |

| scenario | heuristic | haiku-5.5-high |
|---|---|---|
| x01-waf-regex-backtracking | 3/3 | 3/3 |
| x02-latent-bug-valid-config | 0/3 | 0/3 |
| x03-postgres-xid-wraparound | 0/3 | 0/3 |
| x04-transit-gateway-saturation | 0/3 | 0/3 |
| x05-os-update-drops-routes | 0/3 | 3/3 |
| x06-partition-cross-region-failover | 0/3 | 1/3 |
| x07-bgp-policy-term-order | 3/3 | 3/3 |
| x08-runbook-removes-index-capacity | 0/3 | 2/3 |
