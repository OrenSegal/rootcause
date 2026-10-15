# Architecture audit, end of week 1

Scope: every module under `src/rootcause/` at the end of week 1 (18 tests, ruff and ty clean).
Each finding names the principle, where it was, and what fixed it.

One process note first: the week-1 tests were written after the code, so week 1 was
test-covered, not test-driven. The 18 tests became characterization tests for the
refactor below. Week-2 features started from a failing test, with one exception
(resume), recorded in the TDD log.

## Findings

| # | Principle | Where (week 1) | Problem | Fix | Status |
|---|---|---|---|---|---|
| 1 | SSOT | `agent/prompts.py:23`, `agent/loop.py:32`, `telemetry/models.py` | The `submit_diagnosis` JSON schema repeated the `Diagnosis` fields and the `Category` values by hand: three copies of one contract. | The schema is generated from the pydantic model (`application/terminal.py`). A test checks the enum against `Category`. | Fixed. `tests/test_contracts.py` |
| 2 | SSOT | `agent_server.py:48` | `list_scenarios` read `scenario.yaml` by splitting lines, a second parser for a file `Scenario` already parses. | Uses the fixture loader. | Fixed |
| 3 | SSOT | `agent/loop.py:29`, `cli.py:25` | Outcomes were a string `Literal` and exit codes a dict keyed by strings. Nothing tied them together. | `Outcome` is a domain enum. A test fails if an outcome has no exit code. | Fixed. `test_every_outcome_has_an_exit_code` |
| 4 | SSOT | `llm/heuristic.py:68`, `scenario.yaml` | The heuristic pulled the service out of the alert's prose with a regex. The alert format lived only in the YAML text. | `Alert` is structured, and its text format is defined once (`Alert.render` / `Alert.parse`). | Fixed. `test_alert_text_round_trips` |
| 5 | SSOT | `agent/loop.py:170,186` | Each response was serialized twice: once for the run log (`model_dump`) and once for the conversation (`exclude_none`). The log did not record what the model was actually sent. | One serialization. The log stores the exact message appended, which resume depends on. | Fixed |
| 6 | SSOT | `scripts/author_001.py` | One authoring script per scenario, and nothing checked that the committed fixtures matched it. | One `ScenarioBuilder`. A test regenerates every scenario and fails on drift. | Fixed. `test_committed_fixtures_match_the_authoring_script` |
| 7 | DIP | `agent/loop.py:18-20,127` | The application loop imported `mcp.Client`, `MCPServer` and `mcp.types`, and `run()` took an `MCPServer`. The transport leaked into the core. | The loop depends on a `Tool` port. MCP is one adapter behind it. | Fixed. Enforced by import-linter |
| 8 | SRP | `agent/loop.py:127-211` | `Investigator.run` (about 85 lines) handled the conversation, budget, logging, stop reasons and outcome building, using an inner `finish` closure. | Split into `AgentLoop` (turns), `ToolExecutor` (gating, retries, timeouts), and `Transcript` (history built from events). | Fixed |
| 9 | SRP | `agent/loop.py:213-285` | `_execute` mixed answer validation, approval, retries and rendering. | `ToolExecutor` handles calls. The terminal tool validates itself. | Fixed |
| 10 | OCP | `agent/loop.py:150,231` | The terminal tool was hard-coded to `submit_diagnosis`, so the loop could not run a worker with a different answer type without being edited. | `AgentSpec` names the terminal answer model, the tool allowlist and whether gated tools are allowed. Workers and the orchestrator reuse the same loop. | Fixed. Workers and orchestrator reuse the loop unchanged |
| 11 | SRP / layering | `telemetry/store.py:42` | `TelemetryStore` did file IO, querying and mutable world state. | The domain store is pure. Loading lives in `adapters/fixtures.py`. | Fixed |
| 12 | Layering | `evals/runner.py:20` | The composition root (wiring provider, store, server, loop) sat inside the evals package. | `rootcause/composition.py`, the only module that wires adapters together. | Fixed. Enforced: interfaces may not import adapters |
| 13 | Clean architecture | whole package | Layering was a convention with nothing enforcing it. | `import-linter` contracts in `pyproject.toml`, run in CI: domain, application, adapters, composition, interfaces. Application may not import MCP, SQLite, YAML or Typer; `tests/test_contracts.py` additionally limits it to `anthropic.types`. | Fixed. Four contracts, all kept, run in CI |
| 14 | TDD gap | `agent/loop.py:262-279` | The retry path, the no-retry rule for non-idempotent tools, and timeouts had no tests. | Tests added. | Fixed. `tests/test_executor.py` |
| 15 | TDD gap | `llm/anthropic_provider.py` | The only code that talks to the API had never run. | Contract test of the outgoing request through a mock transport, with no network and no spend. | Fixed. `test_request_carries_thinking_effort_cache_and_fallbacks` |
| 16 | TDD gap | `llm/provider.py:49` | The pricing math was untested, and "unknown models are priced safely" was false once a fallback to a more expensive model was possible. | Price table is per adapter, includes fallback targets, and has tests. | Fixed. `tests/test_budget_and_provider.py` |
| 17 | Metric honesty | `evals/grader.py` | `unapproved_actions` cannot be non-zero by construction (denied calls are errors). | Kept as a tripwire. Reported as a structural guarantee backed by a test, not as a measured result. | Kept as a tripwire, documented in `application/evals.py` |

## Deliberate exceptions

- **The application layer uses `anthropic.types`.** The loop speaks the Messages API's
  content-block format (tool_use, tool_result, thinking). Re-declaring those types would
  copy the SDK (an SSOT violation of its own). The rule enforced is narrower: the
  application layer may import the SDK's data types but not its client or resources. That rule
  lives in `tests/test_contracts.py`, because `import-linter` can't name subpackages of
  an external package.

## Found during week 2

| # | Principle | Where | Problem | Fix |
|---|---|---|---|---|
| 18 | ISP | `application/agent.py` `AgentSpec.select` | Choosing an agent's tools needs only a name and a read-only flag, but `select` demanded the full `Tool` port. Found by `ty`. | `ToolTraits` protocol. `Tool` extends it. |
| 19 | Layering | `interfaces/cli.py`, `interfaces/mcp_server.py` | Both built adapters themselves (event log, fixtures, MCP server), so wiring lived in three places. Found by the layer contract. | Composition is its own layer. A fourth contract bars interfaces from importing adapters. |
| 20 | Robustness | `adapters/sqlite_events.py` | `create table if not exists` silently kept a week-1 table layout, and the first insert failed inside an MCP tool whose error the server hides. Found by the stdio smoke test. | Schema version in `pragma user_version`; an old log is refused with a message saying what to do. |
| 21 | Correctness | `application/evals.py` `grade` | Only telemetry ids counted as real evidence, so citing the action id an approved rollback returned was graded as invented. Found by the first Claude Code run (scenario 021). | Ids of rollbacks that ran in the run count as known. The id format has one definition, `TelemetryStore.rollback_action_id`. |
| 22 | Robustness | `adapters/claude_code.py` | A truncated output line raised and aborted the whole suite; a crash, bad sign-in or timeout left no reason behind; a harness error reported as `success` was graded as the model's wrong answer; a cut-off run could show an unapproved rollback as approved. Found by code review. | Unparseable lines, exit codes, stderr and timeouts become an `error` outcome with a note; partial output survives a timeout and the process is always killed; approval requires the operator's yes. |

## TDD log

Week 1 was test-covered, not test-driven. From the refactor on:

| Change | Order | Evidence |
|---|---|---|
| Refactor into layers | Characterization | The 18 week-1 tests stayed green throughout. Two assertion changes, both deliberate: the outcome `diagnosed` became `answered` because workers use the same loop, and the eval-gate test now runs on scenario 001 alone because the full suite is meant to defeat the baseline. |
| Audit gaps 14-16 | Test after code | Code already existed. Two mutants (retrying a non-idempotent tool, skipping the intent log) were each caught by a test. |
| Scenario builder, scenarios 002-007 | Red, then green | Three failures first (no authoring script, missing categories, one scenario), then green. |
| Resume | **Not test-first** | The resume code was written during the refactor, before its tests, so the tests passed on first run. A mutation check then found a gap: disabling "reuse results logged before the crash" survived. `test_tools_that_finished_before_a_mid_turn_crash_are_not_rerun` was added and kills that mutant. |
| Orchestrator and workers | Red, then green | `ModuleNotFoundError` first, then five tests green. |
| Multi mode, `compare` | Red, then green | Same, plus a red-first test that the heuristic refuses multi mode instead of looping to the step limit. |
| GitHub Actions adapter | Red, then green | Two expectations were corrected while still red: the run-level span also fails, and the fixture writer lives in `fixtures.py`. |
| `ci-triage` command | Red, then green | Two failing CLI tests first (comment output, missing input). |
| Event log schema version | Red, then green | Written after the stdio smoke test exposed the bug (#20). |
| CLI tests under GitHub Actions | Red, then green | The first Actions run failed one test: Rich colors and wraps output when `GITHUB_ACTIONS` is set. Reproduced locally with `GITHUB_ACTIONS=true FORCE_COLOR=1` before pinning the test runner's environment. |
| Claude Code harness | Red, then green | Two throwaway `claude -p` runs came first, to learn the stream format, including what a denied rollback looks like. Then seven tests against a scrubbed copy of one run failed with `ModuleNotFoundError` before the adapter existed. |
| Scenarios 009-022 | Red, then green | The suite-size floor was raised to 20 authored scenarios first and failed at 7. Adding the builders without regenerating failed the drift test. Regenerating made both pass, and the baseline still passes only 001 and 005. |
| Grader fix #21 | Red, then green | A test that cites an approved rollback's action id failed first; a denied rollback's id must still count as invented. |
| Claude Code failure handling (#22) | Red, then green | Five tests failed first: a truncated line, an `is_error` result, a cut-off rollback, a failing process with stderr, and a timeout that keeps partial output. A sixth, for a missing `claude` binary, came from the pre-merge check. The timeout test was flaky with a 0.5s window under load and now uses 2s. |
| Token usage, served model, error status | Red, then green | Four tests failed first: per-run usage and models from the loop and from Claude Code's stream, a `max_tokens` stop graded as an error rather than a wrong answer, and a suite pass rate that leaves errors out. |
| Oracle and null checks | Test after code | They check the existing grader, so they passed on first run: on every scenario an answer built from the truth passes, and no answer or a wrong-category answer fails. |
| Statistics, sweeps, `sweep` and `report` commands | Red, then green | Interval and pass^k arithmetic against hand-computed values; summary, frontier, resumability with errors kept out of the rows, and the CLI path, each failing before its code existed. One expectation was wrong while red: a Wilson upper bound of 1.0 is 0.9999999999999999 in floating point. |
| Runtime grounding check | Red, then green | The test failed first. Turning the check on then failed 13 existing tests whose scripted agents cited ids they never fetched; their scripts now fetch what they cite, except the resume tests, whose stub tools return no telemetry and which turn the check off with a comment saying so. |
| Harmful-action grade, scenario 025 | Red, then green | An executed rollback of a deploy outside the truth fails the run. 025's test (obeying the planted instruction fails, ignoring it passes) failed until the scenario existed. |
| Retrieval: corpus, BM25, dense, fusion, tool, `retrieval-eval` | Red, then green | Metric arithmetic, BM25 ranking, fusion, label integrity, a check that no postmortem contains a scenario's evidence ids, the tool appearing only when retrieval is on, and the mode reaching Claude Code's server config, each failing first. Writing the wiring test exposed that Claude Code starts the server in an empty directory, so the corpus path is now absolute. |
| Tracing: `Tracer` port, loop and executor spans, Claude Code spans, OpenTelemetry adapter, `--trace` | Red, then green | Span names, nesting and GenAI attributes were tested against a recording tracer and then against the OpenTelemetry SDK's in-memory exporter, each failing first. The CLI test found that the console exporter binds stdout when its module is imported; it now gets stdout when tracing is configured. |
