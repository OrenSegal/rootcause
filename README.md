# rootcause

An agent that investigates incidents and failed CI runs, written to show how agents work underneath the frameworks: a hand-written loop, tools served over MCP, an approval gate, a crash-safe event log, an orchestrator with workers, and evals scored against known root causes.

It runs three ways: as a CLI with exit codes for CI, as an MCP server a coding agent can call, and as a GitHub Action.

## Quickstart

```bash
uv sync
uv run rootcause eval --min-pass-rate 0.05               # every scenario, no API key needed
uv run rootcause investigate scenarios/007-inventory-timeout-cascade
uv run rootcause ci-triage --raw scenarios/008-sous-shellcheck/raw
uv run rootcause eval --provider claude-code --min-pass-rate 0   # Claude Code drives the tools
uv run rootcause compare --provider heuristic --provider anthropic --budget-usd 0.25
# ^ paid API calls: 48 investigations (24 scenarios, 2 modes), each capped at $0.25
```

By default the investigator is a rule-based baseline that needs no key. `--provider anthropic` runs Claude Opus 5.5 through the hand-written loop on an API key. `--provider claude-code` hands the same tools and brief to headless Claude Code, which needs the `claude` CLI installed and signed in, and runs on whatever account that is.

## What it shows

**The loop, by hand.** `application/agent.py` is the whole cycle: call the model, check the stop reason, run tools, append the results, repeat until the agent submits an answer or hits its step limit, its budget, or a refusal. History is append-only. Each assistant turn goes back exactly as it was logged, thinking blocks included.

**Tools with delivery rules.** Telemetry tools are served by an MCP server (`adapters/mcp_telemetry.py`) and annotated as read-only or not. The executor (`application/executor.py`) turns that into behavior:

- Read-only and idempotent tools are retried with backoff and re-run after a crash (at least once).
- Anything else runs at most once. An intent row is written before the call, so if the process dies between the side effect and its result, a resumed run tells the model the outcome is unknown instead of repeating it.
- A tool that can change production needs operator approval. The gate is in the loop, not the MCP server, because the 2026-07-28 MCP protocol removed server-initiated requests.

**Answers as contracts.** An agent finishes by calling a terminal tool whose schema is generated from a pydantic model (`domain/answers.py`). Strict tool use guarantees the shape; pydantic enforces the bounds the strict schema can't express, and a bad answer goes back to the model to fix.

**Crash recovery.** Every turn, tool call and approval is a row in an append-only SQLite log. `AgentLoop.resume` rebuilds the conversation from those rows and continues, without re-running tools whose results were already logged.

**One loop, three roles.** An `AgentSpec` sets the prompt, the tools an agent may see, whether it may reach gated tools, and its answer type. The single investigator, the orchestrator and each worker are specs over the same loop. Workers get read-only subsets (changes, logs, metrics), run concurrently, share the orchestrator's budget, and are linked to it in the event log.

**Two harnesses, one grader.** `adapters/claude_code.py` runs `claude -p` against the same telemetry MCP server, with built-in tools, user settings and other MCP servers switched off, and with the ground truth left out of the fixtures it serves. Its stream is mapped onto the same `RunResult` the hand-written loop returns, so both are graded by the same code. A rollback is visible to both but only runs when the scenario's operator approves; a denial reaches the model as a tool error.

**Evals that can fail.** Twenty-four scenarios. The twenty-one written as code are each built around a mistake a responder could make: a deploy that lands just before a vendor outage, a flag flipped at runtime with no deploy on record, a retry policy two services away turning a one-minute blip into an outage, a migration hidden in a deploy whose config diff points elsewhere, a maintenance window that looks like an incident. The baseline ("blame the last deploy before the first error") is right on three. Grades check the category, the service, and that every cited id is one the tools returned.

## Results

One run of each over all 24 scenarios, on 2026-10-15. The grades and every diagnosis are in `results/`. The run used the fixtures at commit `6cf3166`; a review afterwards corrected metric values in 012, 014 and 022 that contradicted their own logs.

| provider | mode | pass rate | cost | avg steps | avg tool calls | avg wall |
|---|---|---|---|---|---|---|
| heuristic | single | 12% | $0.0000 | 3.0 | 3.0 | 0.1s |
| Claude Code, Opus 5.5, high effort | single | 75% | $1.5974 | 3.8 | 6.5 | 14.0s |

The cost column is what Claude Code reports the calls would cost at API rates. This run went through a subscription.

What the six failures were:

- **One grader bug.** On 021 the diagnosis was right but cited `rb-d-2101`, the id the rollback tool returned, and the grader only knew telemetry ids. Fixed (`docs/AUDIT.md`, #21); re-grading the same answers gives 19 of 24.
- **Four disagreements about where the cause lives.** The model named the vendor instead of our card gateway (002), cert-manager's failed renewal instead of the ledger's certificate (009), the auth host with the skewed clock instead of NTP (014), and the database under maintenance instead of the paged reporting service (019). Each is defensible. The grader accepts one service per scenario, and I left the truths as they were written before the run rather than fit them to the answers.
- **One category disagreement.** On 012 it called a used-up SMS quota resource exhaustion; the truth says dependency failure.

So the strict score mostly measures attribution, not whether the model found the problem: in every case it found the real trigger and cited evidence for it. Single runs, so small differences between configurations would be noise.

Not run yet: the hand-written loop against a live model (`--provider anthropic`, which needs an API key) and the orchestrator-plus-workers mode.

## Architecture

```
interfaces/   CLI, MCP server for coding agents
composition   the only module that wires adapters together
adapters/     MCP server and client, Anthropic, Claude Code, heuristic, SQLite, fixtures, GitHub Actions
application/  agent loop, executor, budget, orchestrator, evals, ports
domain/       telemetry, scenarios, answers (no framework imports)
```

The layering is enforced, not described: `import-linter` contracts in `pyproject.toml` run in CI, and `tests/test_contracts.py` checks the rules import-linter can't express. `docs/AUDIT.md` lists the SSOT, SOLID and layering problems found in the first version, how each was fixed, and which changes were written test-first.

## Scenarios

`scenarios/001` to `007` and `009` to `022` are written as code in `scripts/author_scenarios.py`, each with a docstring saying what makes it hard. A test regenerates them and fails if the committed fixtures drift, and another fails if the baseline solves more than half. A runtime flag flip or an operator's cache flush shows up as a log line, the way a flag service or a runbook audit log would record it, so neither needed a new tool.

`scenarios/008`, `023` and `024` are real failed runs from [sous](https://github.com/OrenSegal/sous): the raw `gh` output is kept in `raw/`, the fixtures are derived from it, and the ground truth was set by reading the logs and diffs, with the reasoning in each `scenario.yaml` header. In 024 the head commit is a decoy and only the earlier guilty commit counts as evidence.

## Use it from a coding agent

```bash
claude mcp add rootcause -- uv run --directory "$PWD" rootcause mcp
claude mcp add incident-007 -- uv run --directory "$PWD" rootcause telemetry-mcp scenarios/007-inventory-timeout-cascade
```

The first exposes `list_scenarios`, `investigate_incident` and `run_evals`. The second hands a coding agent the raw telemetry tools, so the agent itself can be scored as the investigator.

## Use it in CI

The eval gate, as a reusable action:

```yaml
- uses: OrenSegal/rootcause@main
  with:
    provider: heuristic
    min-pass-rate: "0.05"
```

Triage of a failed run, read-only (it fetches with `gh` and writes a comment body; it never posts):

```bash
rootcause ci-triage --repo owner/name --run 123456789 --provider anthropic --comment-md comment.md
rootcause ci-triage --repo owner/name --run 123456789 --record scenarios/   # keep it as a new scenario
```

## Not done yet

- The hand-written loop and the multi-agent mode against a live model. Both are tested against scripted model turns only.
- Repeated runs per configuration, so differences come with a spread.
- Telemetry recorded from a running service stack. The CI adapter covers real recorded data for now.
- A LangGraph version of the orchestrator to compare against the hand-written one, and OpenTelemetry traces.
- `ci-triage` inside Actions on a `workflow_run` trigger. The CI workflow and the composite action run on every pull request; `ci-triage` has run against live GitHub data from a terminal.
