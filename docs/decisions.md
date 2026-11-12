# Decisions

Five design decisions, each with the evidence that backs it. Where the reasoning at the time was not written down, this says "not recorded" rather than reconstructing it.

## 1. A hand-written agent loop, not a framework

**Context.** The point of the repo is to show what an agent loop does underneath the frameworks: stop reasons, tool results, step and budget limits, refusals, resume.

**Decision.** `application/agent.py` is the whole loop. The orchestrator and its workers are `AgentSpec`s over the same loop.

**Alternatives considered.** LangGraph. What was weighed when the loop was written is not recorded. The LangGraph port (`adapters/langgraph_engine.py`) came afterwards, so the comparison below is evidence that the choice cost nothing detectable, not the reason it was made.

**Consequences.** Every behaviour (append-only history, thinking blocks sent back as logged, nudges, budget) is visible and tested in this repo. The cost is owning that code. The LangGraph port shares everything except control flow, including the executor and its approval gate, so the two can be compared directly.

**Evidence.** Loop first run on a live model in 11367f6 (PR #5); LangGraph port in 2511fed (PR #6). 300 runs, Opus 5.5 at high effort, same model access and grader (`results/sweeps/loop-2026-10-15/`, `results/sweeps/langgraph-2026-10-15/`): single-agent loop minus LangGraph is -2.7 points (-8.0 to +2.7), with only 4 of 25 scenarios differing, and the design detects gaps above about 8 points; cost per solved incident, output tokens and p50 latency are within 6% (gaps from `uv run python scripts/resample_gaps.py`, the rest from each sweep's `report.md`). The tests that drove the port are listed in `docs/AUDIT.md`.

## 2. Telemetry tools served over MCP

**Context.** The same tools had to serve three investigators: the hand-written loop, Claude Code's own loop, and any coding agent a user points at a scenario.

**Decision.** The telemetry tools are an MCP server (`adapters/mcp_telemetry.py`). The hand-written loop reaches it in process through an MCP client (`adapters/mcp_tools.py`); Claude Code and coding agents reach it over stdio (`rootcause telemetry-mcp`). The caller names which tools it trusts as read-only, and a server's `read_only_hint` and `idempotent_hint` count for nothing.

**Alternatives considered.** Plain in-process Python tools. Why they were rejected is not recorded beyond the reuse above; the second harness (Claude Code's own loop) could only be added against the same tools because they were already behind MCP.

**Consequences.** One grader scores every harness against the same tool surface. The MCP transport adds a hop and its own spans in traces. Trusting server hints was a bug, found and fixed (below).

**Evidence.** MCP server in the first non-empty commit (925eed8; d1a526b before it is an empty initial commit); Claude Code as a second harness over it in fca33c6. PR #10 (d3c2545) removed trust in `read_only_hint` in favor of a caller-side `READ_ONLY_TOOLS` list and made `telemetry-mcp` read-only unless started with `--allow-rollback`. `McpTool.idempotent` went on trusting the server's `idempotent_hint` after that PR, so `rollback_deploy` was retried and re-run on resume; it is now a caller-side list too (`mcp_tools(idempotent=...)`), empty for the telemetry tools, and `tests/test_resume.py::test_the_shipped_rollback_is_not_repeated_after_a_crash` covers it.

## 3. An append-only event log for crash recovery

**Context.** A tool that changes production (rollback) must not run twice if the process dies mid-call, and a crashed run should continue rather than start over and re-spend.

**Decision.** Every turn, tool intent, tool result and approval is a row in an append-only SQLite log (`adapters/sqlite_events.py`). Non-idempotent tools get an intent row before the call; on resume, an intent with no result tells the model the outcome is unknown instead of repeating the call. `AgentLoop.resume` rebuilds the conversation from the rows.

**Alternatives considered.** Checkpointing mutable state, or no recovery. Not recorded.

**Consequences.** Resume reproduces the exact history sent to the model, and spend survives a crash. There is no `rootcause resume` command: the log does not record a run's mode, retrieval or approval policy, which a command would need.

**Evidence.** `tests/test_resume.py` (crash between side effect and log is not repeated; finished tools are not re-run; spend is counted) and `tests/test_resume_kill.py` (a killed process resumes without repeating the side effect). PR #10 fixed resume forgetting the `seen` set and worker spend, so a resumed run had repeated work and under-counted money.

## 4. The approval gate lives in the loop, not the MCP server

**Context.** Rollback needs operator approval. The 2026-07-28 MCP protocol removed server-initiated requests, so a server can no longer pause a call to ask the user.

**Decision.** `ToolExecutor` asks an `ApprovalPolicy` before any tool not on the caller's read-only list (`application/executor.py`, `application/approval.py`). The loop sees every call before it is sent, so a misbehaving server cannot bypass it. A denial reaches the model as a tool error.

**Alternatives considered.** Server-side confirmation through MCP elicitation, ruled out by the protocol change above. A human prompt in the loop is not built: the only policy today is a static allow or deny, which keeps evals reproducible.

**Consequences.** Paths that skip the executor need their own gate: Claude Code's own loop is gated by `--allowedTools`, and `telemetry-mcp` given to a coding agent is read-only by default. The grader adds a second check: a rollback against a deploy outside the root cause fails the run even when approved (scenario 025 tests it with a prompt injection; no model followed it in 21 runs).

**Evidence.** `application/approval.py` docstring; `tests/test_loop.py` (gated rollback, denial reaches the model, approved rollback runs once); PR #10 (d3c2545) for the read-only allowlist.

## 5. Intervals resample scenarios, not runs

**Context.** Each configuration runs every scenario three times. The original report put a Wilson interval over all 75 runs, which treats repeats of one scenario as independent. They are not: most scenarios pass or fail all three times.

**Decision.** Sweep summaries use a seeded cluster bootstrap over scenarios (`application/stats.py`, `cluster_bootstrap`), keeping each scenario's runs together. When every run passed (or failed), no resample can show a miss, so the interval is Wilson over scenarios instead. Comparisons are paired by scenario with a minimum detectable effect, an exact sign test on the scenarios that differ, and Holm correction (`scripts/resample_gaps.py`).

**Alternatives considered.** Wilson over runs: too narrow. A mixed-effects or GEE model: not used; why is not recorded, though a bootstrap needs no new dependency and matches how scenarios are sampled.

**Consequences.** Intervals widened 1.50 to 1.60x (intraclass correlation 0.69 to 0.78, about 30 effective runs per setting of 75), and the README's accuracy claims changed from "flat" to "unresolved below about 13 to 19 points". Cost is now the headline.

**Evidence.** 60c0f40 (PR #19); `tests/test_stats.py::test_cluster_bootstrap_resamples_whole_scenarios`; the ICC, effective n and width ratio are printed by `uv run python scripts/resample_gaps.py`.
