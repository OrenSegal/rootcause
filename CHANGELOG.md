# Changelog

## Unreleased

- An externally sourced held-out set: eight incidents adapted from public postmortems (`scenarios-external/`, `scripts/author_external.py`), truths pre-registered in f5417b1 before any run. Haiku 5.5 passes 50% (cluster-bootstrap CI 21% to 79%) against 77% on the main suite; 24 runs in `results/sweeps/external-heldout-2026-11-07/`. `scripts/resample_gaps.py` gains a single-arm cluster-bootstrap interval.
- Eight rule scenarios (026 to 033), accepted services in the truth with `--strict-service` for the old grade, a held-out split in sweep files and the report, and a required search step. A 72-run sweep of them is in `results/sweeps/rule-suite-2026-11-05/`; mutation-testing the retrieval brief with them killed 2 of 14 mutants, both through the search requirement (`results/litmus/2026-11-06-rule-suite.md`).

## 0.2.0 (2026-11-02)

- Fixed the GitHub Action, the approval gate and resume (d3c2545). The telemetry MCP server takes rollback as an explicit option and is read-only by default over stdio; the loop gates on its own list of read-only tools instead of server hints.
- Added a Haiku 5.5 sweep of the hand-written loop, 75 runs (`results/sweeps/loop-haiku-2026-10-26/`).
- Rewrote README claims about the grader and sweep results to match the committed result files.

## 0.1.0

- First tagged version: agent loop, MCP telemetry tools, approval gate, SQLite event log, orchestrator with workers, retrieval over postmortems, OpenTelemetry traces, 25 scenarios, sweeps and reports, GitHub Action.
