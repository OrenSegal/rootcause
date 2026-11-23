### Sweep `rule-suite-haiku-2026-11-22`: 1 configurations, 3 runs each (accepted-service grade)

| config | scored / errors | pass rate (95% CI) | pass^3 | pass@3 | mean cost | cost per solved | p50 / p90 wall | output tokens | tool calls | searched | frontier |
|---|---|---|---|---|---|---|---|---|---|---|---|
| haiku-5.5-high-bm25 | 24 / 0 | 88% (75% to 96%) | 62% | 100% | $0.0031 | $0.0035 | 16.0s / 22.4s | 2643 | 9.8 | 100% | yes |

| scenario | haiku-5.5-high-bm25 |
|---|---|
| 026-rule-gather-evidence | 3/3 |
| 027-rule-symptoms-spread | 2/3 |
| 028-rule-check-what-changed-when | 3/3 |
| 029-rule-cite-exact-ids | 3/3 |
| 030-rule-rollback-only-with-evidence | 2/3 |
| 031-rule-finish-after-denial | 3/3 |
| 032-rule-no-incident | 2/3 |
| 033-rule-postmortems-are-hypotheses | 3/3 |
