# rootcause

An agent that investigates incidents and failed CI runs, written to show how agents work underneath the frameworks: a hand-written loop, tools served over MCP, an approval gate, a crash-safe event log, an orchestrator with workers, and evals scored against known root causes.

It runs three ways: as a CLI with exit codes for CI, as an MCP server a coding agent can call, and as a GitHub Action.

## Quickstart

```bash
uv sync
uv run rootcause eval --min-pass-rate 0.2                # every scenario, no API key needed
uv run rootcause investigate scenarios/007-inventory-timeout-cascade
uv run rootcause ci-triage --raw scenarios/008-sous-shellcheck/raw
uv run rootcause compare --provider heuristic --provider anthropic --budget-usd 0.25
# ^ paid: 16 investigations (8 scenarios, 2 modes), each capped at $0.25
```

Without `--provider anthropic`, the investigator is a rule-based baseline that needs no key. With it, Claude Opus 5.5 runs the same loop.

## What it shows

**The loop, by hand.** `application/agent.py` is the whole cycle: call the model, check the stop reason, run tools, append the results, repeat until the agent submits an answer or hits its step limit, its budget, or a refusal. History is append-only. Each assistant turn goes back exactly as it was logged, thinking blocks included.

**Tools with delivery rules.** Telemetry tools are served by an MCP server (`adapters/mcp_telemetry.py`) and annotated as read-only or not. The executor (`application/executor.py`) turns that into behavior:

- Read-only and idempotent tools are retried with backoff and re-run after a crash (at least once).
- Anything else runs at most once. An intent row is written before the call, so if the process dies between the side effect and its result, a resumed run tells the model the outcome is unknown instead of repeating it.
- A tool that can change production needs operator approval. The gate is in the loop, not the MCP server, because the 2026-07-28 MCP protocol removed server-initiated requests.

**Answers as contracts.** An agent finishes by calling a terminal tool whose schema is generated from a pydantic model (`domain/answers.py`). Strict tool use guarantees the shape; pydantic enforces the bounds the strict schema can't express, and a bad answer goes back to the model to fix.

**Crash recovery.** Every turn, tool call and approval is a row in an append-only SQLite log. `AgentLoop.resume` rebuilds the conversation from those rows and continues, without re-running tools whose results were already logged.

**One loop, three roles.** An `AgentSpec` sets the prompt, the tools an agent may see, whether it may reach gated tools, and its answer type. The single investigator, the orchestrator and each worker are specs over the same loop. Workers get read-only subsets (changes, logs, metrics), run concurrently, share the orchestrator's budget, and are linked to it in the event log.

**Evals that can fail.** Eight scenarios, most built around a mistake a responder could make: a decoy deploy just before an outage at a vendor, a memory leak with no deploy nearby, a false alarm, a database failover during a config change. The baseline ("blame the last deploy before the first error") is right on two of them by design. Grades check the category, the service, and that every cited evidence id exists in the telemetry.

## Results so far

| provider | mode | pass rate |
|---|---|---|
| heuristic baseline | single | 2 of 8 |
| Claude Opus 5.5 | single | not run yet |
| Claude Opus 5.5 | orchestrator plus workers | not run yet |

The model rows wait on a funded run. `rootcause compare` produces this table, with cost, steps, tool calls and wall time per row.

## Architecture

```
interfaces/   CLI, MCP server for coding agents
composition   the only module that wires adapters together
adapters/     MCP server and client, Anthropic, heuristic, SQLite, fixtures, GitHub Actions
application/  agent loop, executor, budget, orchestrator, evals, ports
domain/       telemetry, scenarios, answers (no framework imports)
```

The layering is enforced, not described: `import-linter` contracts in `pyproject.toml` run in CI, and `tests/test_contracts.py` checks the rules import-linter can't express. `docs/AUDIT.md` lists the SSOT, SOLID and layering problems found in the first version, how each was fixed, and which changes were written test-first.

## Scenarios

`scenarios/001` to `007` are written as code in `scripts/author_scenarios.py`. A test regenerates them and fails if the committed fixtures drift. `scenarios/008` is a real failed run from [sous](https://github.com/OrenSegal/sous): the raw `gh` output is kept in `raw/`, the fixtures are derived from it, and the ground truth was set by reading the log.

## Use it from a coding agent

```bash
claude mcp add rootcause -- uv run --directory "$PWD" rootcause mcp
claude mcp add incident-007 -- uv run --directory "$PWD" rootcause telemetry-mcp scenarios/007-inventory-timeout-cascade
```

The first exposes `list_scenarios`, `investigate_incident` and `run_evals`. The second hands a coding agent the raw telemetry tools, so the agent itself can be scored as the investigator.

## Use it in CI

The eval gate, as a reusable action (once this repository is on GitHub):

```yaml
- uses: OrenSegal/rootcause@main
  with:
    provider: heuristic
    min-pass-rate: "0.2"
```

Triage of a failed run, read-only (it fetches with `gh` and writes a comment body; it never posts):

```bash
rootcause ci-triage --repo owner/name --run 123456789 --provider anthropic --comment-md comment.md
rootcause ci-triage --repo owner/name --run 123456789 --record scenarios/   # keep it as a new scenario
```

## Not done yet

- Model results for the table above.
- Telemetry recorded from a running service stack. The CI adapter covers real recorded data for now.
- A LangGraph version of the orchestrator to compare against the hand-written one, and OpenTelemetry traces.
- Nothing here has run inside GitHub Actions yet: not the CI workflow, the action, or `ci-triage`. `ci-triage` has run against live GitHub data from a terminal.
