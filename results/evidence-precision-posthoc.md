# Evidence precision: a post-hoc second grade

Written by `scripts/regrade_evidence.py` from the committed `runs.sqlite` files, after the results were public. The main grade and every published pass rate are unchanged. The floors below were not chosen before the runs and no truth lists decoy ids to forbid, so read this as a description of the grader, not a result about the models.

**The main grade does not penalise over-citation.** Right category and service plus every id in the scenario's telemetry passes 41 of 41 scenarios (median 23 ids per scenario).

Precision is the share of an answer's cited ids that are in the truth's `required_evidence_any`. "Still pass" counts runs that pass the main grade and meet the floor.

| sweep | provider, mode | answers | pass, main grade | ids cited, median | precision, median | still pass at 10% | still pass at 25% | still pass at 50% |
|---|---|---|---|---|---|---|---|---|
| models-and-retrieval-2026-10-15 | claude-code:claude-haiku-5-5:high, single | 75 | 58 | 8 | 0.23 | 58 | 32 | 1 |
| models-and-retrieval-2026-10-15 | claude-code:claude-opus-5-5:high, single | 227 | 180 | 8 | 0.25 | 177 | 127 | 5 |
| models-and-retrieval-2026-10-15 | claude-code:claude-opus-5-5:low, single | 75 | 59 | 6 | 0.22 | 58 | 31 | 7 |
| models-and-retrieval-2026-10-15 | claude-code:claude-opus-5-5:medium, single | 75 | 61 | 8 | 0.25 | 57 | 42 | 1 |
| models-and-retrieval-2026-10-15 | claude-code:claude-sonnet-5-5:high, single | 75 | 60 | 7 | 0.29 | 60 | 44 | 3 |
| models-and-retrieval-2026-10-15 | all | 527 | 418 | 8 | 0.25 | 410 | 276 | 17 |
| loop-2026-10-15 | claude-code-loop:claude-opus-5-5:high, multi | 75 | 58 | 11 | 0.20 | 55 | 11 | 0 |
| loop-2026-10-15 | claude-code-loop:claude-opus-5-5:high, single | 75 | 61 | 10 | 0.22 | 56 | 33 | 0 |
| loop-2026-10-15 | all | 150 | 119 | 10 | 0.22 | 111 | 44 | 0 |
| langgraph-2026-10-15 | langgraph:claude-opus-5-5:high, multi | 75 | 61 | 11 | 0.20 | 56 | 17 | 0 |
| langgraph-2026-10-15 | langgraph:claude-opus-5-5:high, single | 75 | 63 | 9 | 0.22 | 58 | 25 | 0 |
| langgraph-2026-10-15 | all | 150 | 124 | 10 | 0.21 | 114 | 42 | 0 |

The models sweep log holds 527 answers: the 525 graded rows and the two runs listed in its `EXCLUDED.md`, which fail the main grade. Its three Opus high-effort configurations (no search, BM25, hybrid) share one provider name in the log.
