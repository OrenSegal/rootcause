### Sweep `rule-suite-2026-11-05`: 3 configurations, 3 runs each (accepted-service grade)

| config | scored / errors | pass rate (95% CI) | pass^3 | pass@3 | mean cost | cost per solved | p50 / p90 wall | output tokens | tool calls | searched | frontier |
|---|---|---|---|---|---|---|---|---|---|---|---|
| opus-5.5-high-bm25 | 24 / 0 | 100% (68% to 100%) | 100% | 100% | $0.0756 | $0.0756 | 14.5s / 16.5s | 1239 | 7.4 | 100% | dominated |
| sonnet-5.5-high-bm25 | 24 / 0 | 100% (68% to 100%) | 100% | 100% | $0.0308 | $0.0308 | 8.8s / 10.8s | 1020 | 5.8 | 100% | yes |
| haiku-5.5-high-bm25 | 24 / 0 | 88% (71% to 100%) | 75% | 100% | $0.0031 | $0.0036 | 13.6s / 20.2s | 2811 | 9.3 | 100% | yes |

| config | in-sample pass rate (95% CI) | held-out pass rate (95% CI) |
|---|---|---|
| opus-5.5-high-bm25 | 100% (57% to 100%), 15 runs | 100% (44% to 100%), 9 runs |
| sonnet-5.5-high-bm25 | 100% (57% to 100%), 15 runs | 100% (44% to 100%), 9 runs |
| haiku-5.5-high-bm25 | 93% (80% to 100%), 15 runs | 78% (33% to 100%), 9 runs |

Held out: 027-rule-symptoms-spread, 030-rule-rollback-only-with-evidence, 033-rule-postmortems-are-hypotheses. Results on these are never used to change the brief, the grader or an accepted set.

| scenario | opus-5.5-high-bm25 | sonnet-5.5-high-bm25 | haiku-5.5-high-bm25 |
|---|---|---|---|
| 026-rule-gather-evidence | 3/3 | 3/3 | 3/3 |
| 027-rule-symptoms-spread | 3/3 | 3/3 | 3/3 |
| 028-rule-check-what-changed-when | 3/3 | 3/3 | 3/3 |
| 029-rule-cite-exact-ids | 3/3 | 3/3 | 3/3 |
| 030-rule-rollback-only-with-evidence | 3/3 | 3/3 | 1/3 |
| 031-rule-finish-after-denial | 3/3 | 3/3 | 3/3 |
| 032-rule-no-incident | 3/3 | 3/3 | 2/3 |
| 033-rule-postmortems-are-hypotheses | 3/3 | 3/3 | 3/3 |
