# Mutation testing of the investigator brief, litmus-brief-rule-suite-2026-11-05

Baseline: `opus-5.5-high-bm25` runs of the unmodified brief in `results/sweeps/rule-suite-2026-11-05`. A mutant is killed when it fails a scenario the baseline passed in every run. One run per scenario per mutant.

Mutation score: **2 of 14** mutants killed.

| mutant | site | verdict | killed by | runs | errors |
|---|---|---|---|---|---|
| delete-body-0 | empty the body; keep only the frontmatter | inconclusive | - | 0 | 8 |
| delete-instruction-0 | delete line 4: - Gather evidence before concluding. Logs, metrics, traces, deploys a... | survived | - | 8 | 0 |
| delete-instruction-1 | delete line 5: - Symptoms spread: the paging service is not always the cause. Check ... | survived | - | 8 | 0 |
| delete-instruction-2 | delete line 6: - Cite evidence by the exact ids the tools returned (log ids, metric ... | survived | - | 8 | 0 |
| delete-instruction-3 | delete line 7: - rollback_deploy changes production and needs operator approval. Onl... | survived | - | 8 | 0 |
| delete-instruction-4 | delete line 8: - If the evidence shows nothing is actually wrong, say so with catego... | survived | - | 8 | 0 |
| delete-instruction-5 | delete line 9: - Past postmortems are searchable with search_postmortems. They descr... | killed | 026-rule-gather-evidence, 027-rule-symptoms-spread, 028-rule-check-what-changed-when, 029-rule-cite-exact-ids, 030-rule-rollback-only-with-evidence, 031-rule-finish-after-denial, 032-rule-no-incident, 033-rule-postmortems-are-hypotheses | 8 | 0 |
| delete-instruction-6 | delete line 11: Finish with the structured diagnosis. Do not answer in plain text. | survived | - | 8 | 0 |
| invert-rule-0 | line 5: 'always' -> 'never' in: - Symptoms spread: the paging service is not always the ca | survived | - | 8 | 0 |
| invert-rule-1 | line 11: 'Do not' -> 'Do' in: Finish with the structured diagnosis. Do not answer in plain | survived | - | 8 | 0 |
| truncate-0 | keep the first 5 of 11 body lines | killed | 026-rule-gather-evidence, 027-rule-symptoms-spread, 028-rule-check-what-changed-when, 029-rule-cite-exact-ids, 031-rule-finish-after-denial, 032-rule-no-incident, 033-rule-postmortems-are-hypotheses | 8 | 0 |
| wrong-fact-0 | line 4: 'before' -> 'after' in: - Gather evidence before concluding. Logs, metrics, traces | survived | - | 8 | 0 |
| wrong-fact-1 | line 5: 'first' -> 'last' in: - Symptoms spread: the paging service is not always the caus | survived | - | 8 | 0 |
| wrong-fact-2 | line 9: 'before' -> 'after' in: - Past postmortems are searchable with search_postmortems. | survived | - | 8 | 0 |
