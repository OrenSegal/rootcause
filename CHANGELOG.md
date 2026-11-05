# Changelog

## 0.2.0 (2026-11-02)

- Fixed the GitHub Action, the approval gate and resume (d3c2545). The telemetry MCP server takes rollback as an explicit option and is read-only by default over stdio; the loop gates on its own list of read-only tools instead of server hints.
- Added a Haiku 5.5 sweep of the hand-written loop, 75 runs (`results/sweeps/loop-haiku-2026-10-26/`).
- Rewrote README claims about the grader and sweep results to match the committed result files.

## 0.1.0

- First tagged version: agent loop, MCP telemetry tools, approval gate, SQLite event log, orchestrator with workers, retrieval over postmortems, OpenTelemetry traces, 25 scenarios, sweeps and reports, GitHub Action.
