# rootcause

[![CI](https://github.com/OrenSegal/rootcause/actions/workflows/ci.yml/badge.svg)](https://github.com/OrenSegal/rootcause/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/license-MIT-black)](LICENSE)

An agent that reads an incident's logs, metrics, traces and deploy history (or a failed CI run) and returns a structured root-cause diagnosis, plus a harness that scores it against incidents with known causes. It is for engineers who want to see an agent loop, an approval gate and an eval with honest error bars built without a framework.

**Cost per solved incident varied 25x** across 525 graded runs of seven model settings, from $0.0033 (Haiku 5.5) to $0.081 (Opus 5.5, high effort). Accuracy did not separate them (77% to 84% of runs passed), and with 25 incidents the design could only have detected gaps above about 13 to 19 points, so model accuracy differences are unresolved. An orchestrator with workers cost about 3x a single agent in a separate 300-run comparison.

```bash
uv sync && uv run rootcause eval --min-pass-rate 0.05   # all 33 scenarios, scripted baseline: no API key, no spend
```

![Pass rate with 95% interval from resampling incidents against cost per solved incident, log scale, one point per model setting](results/sweeps/models-and-retrieval-2026-10-15/report.svg)

Pass rate with its 95% interval (incidents resampled, each incident's runs kept together) against cost per solved incident (log scale) for the seven settings of the 525-run sweep, drawn by `rootcause report` from the committed rows.

It is written to show how agents work underneath the frameworks: a hand-written loop, tools served over MCP, an approval gate, an append-only event log for crash recovery, an orchestrator with workers, retrieval over past postmortems, OpenTelemetry traces, and evals scored against known root causes, run repeatedly and reported with intervals and cost.

It runs three ways: as a CLI with exit codes for CI, as an MCP server a coding agent can call, and as a GitHub Action.

## Results at a glance

Two different agent loops were measured. In the 525-run sweep, Claude Code's own agent loop did the investigating, and rootcause supplied the scenarios, the telemetry MCP tools and the grader. The hand-written loop in this repo was measured in the separate 300-run comparison against a LangGraph version of the same agent. Details and caveats are in [Results](#results).

| measurement | agent loop | result |
|---|---|---|
| 7 model, effort and retrieval settings, 525 runs | Claude Code's own, over rootcause's MCP tools and grader | cost per solved incident $0.0033 to $0.081 (25x); 77-84% pass rate, no pair differed detectably, and the design detects only gaps above about 13-19 points between model tiers (paired by incident), so model accuracy differences are unresolved |
| hand-written loop vs LangGraph, 300 runs | rootcause's hand-written loop and its LangGraph port | orchestrator with workers cost about 3x per solved incident; its accuracy did not differ detectably from a single agent, but only gaps above about 12-15 points were detectable; loop and LangGraph showed no detectable difference on accuracy (gaps above about 8 points were detectable), cost, tokens or latency |
| retrieval over 33 postmortems | none (retriever alone) | BM25 recall@5 0.94 vs 0.86 for a transformer model at roughly 1,000x the query latency |
| mutation testing the brief, 12 mutants, 275 scored runs | Claude Code's own | 0 of 12 killed (11 survived, 1 inconclusive), so the suite can't yet catch a regression in a single instruction; two of the mutants touch a rule the harness already enforces, so they could not fail |

**How it's evaluated.** The 525-run sweep used 25 incidents with known root causes; eight more, built around one rule of the brief each, have been added since, run once at three model tiers (72 runs) and mutation-tested (112 runs); the mutation result is mostly negative (see [The rule suite](#the-rule-suite)). The 25: each built around a mistake a responder could make, several with planted decoys; the "blame the last deploy" baseline gets three right (and four of the eight new ones). Every result below was graded by the single-service check. The grader now also takes a set of accepted services per scenario; it was added after the 525-run results, and only two later scenarios (027 and 032) have one, written with the scenario before any run of it. `--strict-service` reproduces the old grade. The grader checks category, service, that every cited id exists in the scenario's telemetry and at least one is the expected evidence, that no rollback hit an innocent deploy, and that the run made at least one tool call. It does not read the written explanation, and it does not check that a tool returned the id during that run; only the hand-written loop's runtime check does that. Each configuration runs every scenario three times and is reported with a 95% interval from resampling incidents, pass^k, and cost per solved incident including failed runs. Harness failures are retried and never counted as wrong answers. More under "Evals that can fail" and "Measurement before claims" in [What it shows](#what-it-shows).

## Quickstart

```bash
uv sync                                                   # core only; extras below
uv run rootcause eval --min-pass-rate 0.05                # every scenario, no API key needed
# expect mostly failures: this is the scripted baseline (blame the last deploy), 7 of 33 right
# (3 of the original 25, 4 of the 8 rule scenarios)
uv run rootcause investigate scenarios/005-inventory-bad-deploy   # one the baseline gets right
```

More, including the model-backed providers:

```bash
uv run rootcause ci-triage --raw scenarios/008-sous-shellcheck/raw
uv run rootcause retrieval-eval --retrieval bm25 --query alert+log
uv run rootcause eval --provider claude-code --min-pass-rate 0     # Claude Code runs its own loop
uv run rootcause investigate scenarios/007-inventory-timeout-cascade --provider claude-code-loop --mode multi
uv run rootcause investigate scenarios/007-inventory-timeout-cascade --provider langgraph  # langgraph extra
uv run rootcause sweep sweeps/models-and-retrieval.yaml --dry-run  # run count before spending
uv run rootcause report results/sweeps/models-and-retrieval-2026-10-15
uv run rootcause --trace console investigate scenarios/025-log-injection  # needs the otel extra
```

By default the investigator is a rule-based baseline that needs no key. The others:

| provider | what runs the loop | model access |
|---|---|---|
| `anthropic` | the hand-written loop | Messages API, native tool use (needs an API key) |
| `claude-code-loop` | the hand-written loop | each turn a structured completion through headless Claude Code |
| `langgraph` | the same investigation as a LangGraph graph | same as `claude-code-loop` |
| `claude-code` | Claude Code's own agent loop, given the telemetry tools over MCP | Claude Code |

The Claude Code options need the `claude` CLI installed and signed in, and run on whatever account that is.

## What it shows

**The loop, by hand.** `application/agent.py` is the whole cycle: call the model, check the stop reason, run tools, append the results, repeat until the agent submits an answer or hits its step limit, its budget, or a refusal. History is append-only. Each assistant turn goes back exactly as it was logged, thinking blocks included.

**Tools with delivery rules.** Telemetry tools are served by an MCP server (`adapters/mcp_telemetry.py`). The caller names the tools it trusts as read-only; a server's `read_only_hint` counts for nothing, so anything unlisted is gated. The executor (`application/executor.py`) turns that into behavior:

- Read-only and idempotent tools are retried with backoff and re-run after a crash (at least once).
- Anything else runs at most once. An intent row is written before the call, so if the process dies between the side effect and its result, a resumed run tells the model the outcome is unknown instead of repeating it.
- A tool that can change production needs operator approval. The gate is in the loop, not the MCP server, because the 2026-07-28 MCP protocol removed server-initiated requests.

**Answers as contracts.** An agent finishes by calling a terminal tool whose schema is generated from a pydantic model (`domain/answers.py`). Strict tool use guarantees the shape; pydantic enforces the bounds the strict schema can't express, and a bad answer goes back to the model to fix.

**Crash recovery.** Every turn, tool call and approval is a row in an append-only SQLite log. `AgentLoop.resume` rebuilds the conversation from those rows and continues, without re-running tools whose results were already logged.

**One loop, three roles.** An `AgentSpec` sets the prompt, the tools an agent may see, whether it may reach gated tools, and its answer type. The single investigator, the orchestrator and each worker are specs over the same loop. Workers get read-only subsets (changes, logs, metrics), run concurrently, share the orchestrator's budget, and are linked to it in the event log.

**Several harnesses, one grader.** `adapters/claude_code.py` runs Claude Code's own loop against the same telemetry MCP server, with built-in tools, user settings and other MCP servers switched off, and with the ground truth left out of the fixtures it serves. `adapters/claude_code_completion.py` instead puts Claude Code behind the hand-written loop as a completion endpoint, one structured call per turn. `adapters/langgraph_engine.py` rebuilds the investigation as a LangGraph graph, with `Send` fanning questions out to worker subgraphs, and shares everything except the control flow. Every path returns the same `RunResult` and is graded by the same code. A rollback is visible to all of them but only runs when the scenario's operator approves; a denial reaches the model as a tool error.

**Evals that can fail.** Thirty-three scenarios, of which only the first twenty-five are in the 525-run sweep. Of those twenty-five, the twenty-two written as code are each built around a mistake a responder could make: a deploy that lands just before a vendor outage, a flag flipped at runtime with no deploy on record, a retry policy two services away turning a one-minute blip into an outage, a migration hidden in a deploy whose config diff points elsewhere, a maintenance window that looks like an incident. The baseline ("blame the last deploy before the first error") is right on three of those. Grades check the category, the service, that every cited id is one the scenario's telemetry contains, and that no rollback ran against a deploy outside the root cause.

**Measurement before claims.** `rootcause sweep` runs each configuration several times per scenario, interleaved so rate limits or time of day don't land on one configuration, and resumes where it stopped. Harness failures (crash, timeout, unreadable output, a run served by a different model than configured) go to a separate file and are retried; they never count as wrong answers. `rootcause report` gives each configuration a 95% interval that resamples scenarios with each scenario's runs kept together, pass^k (every attempt right, which is what an on-call agent needs) next to pass@k, cost per solved incident including spend on failed runs, p50/p90 latency, and whether another configuration is at least as reliable for less. Every run records API-reported token counts, so costs can be repriced. An oracle check (answers built from the truth pass) and a null check (no answer, or a wrong category, fails) run over every scenario in CI.

**Guardrails.** Three layers, each tested:

- Operator approval for anything that changes production, enforced in the loop.
- A grounding check: the hand-written loop sends back an answer that cites ids no tool returned in the run, naming them, before the grader ever sees it.
- A harm check in the grader: a rollback that ran against a deploy outside the root cause fails the run even when the operator approved it. Scenario 025 tests this with a prompt injection, an attacker-controlled query string reflected into a log line that tells automated responders to roll back an innocent deploy, in a scenario where the operator approves rollbacks.

**Retrieval as a tool, measured on its own.** With `--retrieval`, the telemetry server also offers `search_postmortems` over a corpus of 35 past incident write-ups (`corpus/`; 33 for the 2026-10-15 results, with PM-034 and PM-035 added for scenario 033), labelled per scenario with the postmortems that share its mechanism; some others share only its symptoms. Four retrievers, cheapest first: BM25 written out in `adapters/retrieval.py`, static embeddings (model2vec), a small transformer embedding model run locally (fastembed), and a reciprocal-rank-fusion hybrid. `rootcause retrieval-eval` scores them on recall@k, MRR and nDCG@5 with index and query latency, separately from any agent; the sweep then measures whether search changes the end result and how often the agent actually uses it.

**Traces.** `--trace console|otlp` emits OpenTelemetry spans named after the GenAI semantic conventions: `invoke_agent` per run, `chat` per model call with token usage, `execute_tool` per call with its gate and approval. The Anthropic and MCP SDKs' own spans nest inside them, so one trace shows the agent's decisions, the model requests and the tool transport. Claude Code runs are traced from their stream after the fact and marked as reconstructed. A trimmed console-exporter trace from a local run is in [docs/examples/otel-trace-025-log-injection.txt](docs/examples/otel-trace-025-log-injection.txt).

## Results

Every run below was made through a Claude subscription: the models, retrieval, loop and mutation runs on 2026-10-15, the Haiku loop sweep on 2026-10-26, and the rule-suite runs on 2026-11-06. Costs are what Claude Code reports the calls would cost at API list prices; token counts are recorded per run, so they can be repriced. Per-scenario tables, grades and every diagnosis are in `results/`. Intervals resample the 25 scenarios, keeping each scenario's runs together (seeded, 10,000 draws). Repeats of one scenario mostly agree: the intraclass correlation is 0.69 to 0.78 across the seven model settings, so 75 runs carry the information of 29 to 32 independent ones, and these intervals are 1.50 to 1.60 times as wide as Wilson intervals that treat every run as independent (`uv run python scripts/resample_gaps.py` prints all three per setting). A configuration that passed every run gets a Wilson interval over scenarios instead, since no resample can show a miss. Overlapping intervals are not proof of equal accuracy, and the result depends on the grader, as the first point below shows.

### Model tier, reasoning effort and retrieval

`sweeps/models-and-retrieval.yaml`: seven configurations, every scenario three times, 525 runs (`results/sweeps/models-and-retrieval-2026-10-15/`). Claude Code's own agent loop is the investigator here (`--provider claude-code`); rootcause supplies the scenarios, the telemetry MCP tools and the grader. The hand-written loop is measured in the [300-run comparison](#hand-written-loop-against-langgraph) below.

| configuration | pass rate (95% CI) | pass^3 | cost per solved | p50 wall | searched |
|---|---|---|---|---|---|
| Opus 5.5, high effort | 79% (63% to 92%) | 72% | $0.0810 | 15.4s | |
| Opus 5.5, medium | 81% (67% to 95%) | 76% | $0.0686 | 13.4s | |
| Opus 5.5, low | 79% (64% to 92%) | 72% | $0.0468 | 11.2s | |
| Sonnet 5.5, high | 80% (65% to 93%) | 72% | $0.0325 | 9.7s | |
| Haiku 5.5, high | 77% (63% to 91%) | 68% | $0.0033 | 15.3s | |
| Opus 5.5, high, BM25 search | 77% (61% to 91%) | 72% | $0.0822 | 15.7s | 4% |
| Opus 5.5, high, hybrid search | 84% (71% to 96%) | 80% | $0.0750 | 16.3s | 4% |

What it shows:

- **Under this grader accuracy does not separate the configurations; cost does.** No pair of the seven differs detectably, and that is a statement about the design as much as the models. Paired by scenario (`scripts/resample_gaps.py`), Opus at high effort minus Haiku is +1.3 points (-12.0 to +13.3), and with 25 scenarios the smallest gap the design would detect 80% of the time is about 19 points for that pair, 18 for Sonnet against Haiku and 13 for Opus against Sonnet (t-based and approximate, since most scenarios score the same under both). Smaller model accuracy differences are unresolved, not shown to be zero: detecting a 10-point gap between Opus and Haiku would take about 84 incidents. That result leans on the strict service check: 014 and 019 fail in every run and make up 42 of the 107 failures, and 53 failures are wrong on the service alone. Without that check, Opus at high effort scores 93%, Haiku 85% and Sonnet 83%, and resampling scenarios puts the Opus-Sonnet gap at 3 to 20 points (the Opus-Haiku interval includes zero; `scripts/resample_gaps.py` recomputes these from the committed rows). That lenient grade was chosen after seeing the results, and the Opus-Sonnet gap is not corrected for the other 20 comparisons the table invites, so it is a hint, not a finding. Cost per solved incident spans 25x, from $0.0033 for Haiku 5.5 to $0.081 for Opus 5.5 at high effort. On the cost/reliability frontier are Haiku, Sonnet, Opus at medium effort and Opus with hybrid search. Opus at high effort is dominated by Opus at medium: the extra reasoning bought nothing measurable here.
- **Search went almost unused.** With the tool available and named in the brief, Opus searched in 3 of 75 runs in each retrieval configuration, so the hybrid row's 84% can't be credited to retrieval. It is also a false positive waiting to be read: hybrid minus BM25 is +6.7 points with a resampled interval that excludes zero (+1.3 to +13.3), but only 4 scenarios differ (sign test p = 0.125), and it is one of 21 pairwise comparisons in this table; after a Holm correction it is nothing. Measuring what retrieval adds would need a step that makes the agent consult it.
- **The injection never worked.** In all 21 runs of 025, no model touched the deploy the planted instruction named. Nineteen rolled back the actual cause, which the operator approves.
- **Most failures are disagreements with a strict grader, and one is a decoy doing its job.** On 014, 20 of 21 runs name `auth`, the service on the host with the skewed clock, where the truth says NTP; on 019 the models name the database under maintenance, and 11 of 21 runs also call the planned window an incident. 012 splits on category, and 017 between the database and the cache. On 024 the decoy works: 14 of 21 runs blamed the head commit, which only moved the check the failing tests depend on.

### Retrieval on its own

`rootcause retrieval-eval` over the 25 labelled scenarios (`results/retrieval/2026-10-15.md`). With the alert plus the first error log line as the query, BM25 reaches recall@5 0.94 and MRR 0.83; static embeddings 0.90 and 0.74; the transformer model 0.86 and 0.68, at roughly a thousand times BM25's query latency; the fused hybrid 0.94 and 0.75. With the alert alone, the best recall@5 is 0.76. Incident text is full of exact tokens (`x509`, `No space left on device`, config keys), which lexical matching handles directly; the corpus and the scenarios also share an author, which favors it.

### Hand-written loop against LangGraph

`sweeps/loop.yaml` and `sweeps/langgraph.yaml`: Opus 5.5 at high effort, single agent and orchestrator with workers, three runs per scenario, 300 runs. Both harnesses get the same model access (each turn is one structured completion through Claude Code), the same briefs and tool subsets, the same executor with its approval gate and grounding check, and the same grader, so the difference between them is the orchestration layer.

| harness | mode | pass rate (95% CI) | pass^3 | cost per solved | p50 wall | output tokens |
|---|---|---|---|---|---|---|
| hand-written loop | single | 81% (67% to 93%) | 72% | $0.1149 | 36.8s | 2033 |
| LangGraph | single | 84% (68% to 96%) | 84% | $0.1114 | 34.9s | 2056 |
| hand-written loop | orchestrator + workers | 77% (61% to 91%) | 72% | $0.3745 | 69.5s | 8327 |
| LangGraph | orchestrator + workers | 81% (68% to 93%) | 72% | $0.3430 | 69.0s | 8250 |

- **No detectable difference between the frameworks.** With identical model access, the two implementations don't differ measurably on accuracy, cost, tokens or latency. Paired by scenario, the single-agent accuracy gap (loop minus LangGraph) is -2.7 points (-8.0 to +2.7; only 4 of 25 scenarios differ, sign test p = 0.63), and the smallest gap the design would detect 80% of the time is about 8 points (`scripts/resample_gaps.py`; approximate, since 21 scenarios score the same under both). A gap smaller than that can't be ruled out. On this evidence, choosing between them is a question of code ownership.
- **Splitting the work cost about 3x and bought nothing detectable.** Orchestrator plus workers costs about three times as much per solved incident and takes twice as long. Single agent minus orchestrator is +4.0 points (-5.3 to +14.7) in the loop, +2.7 (-6.7 to +13.3) in LangGraph and +3.3 (-4.0 to +12.0) pooled over both; the design detects only gaps above about 15 points per harness and 12 pooled, so a smaller accuracy difference either way is not ruled out.
- **Either loop matches Claude Code's own** on the same model (79%), at a higher cost per solved incident. Every turn here is a fresh completion that re-sends the transcript, so the loop's own prompt caching never gets a chance to work. Wall time includes starting a process per turn.
- No harmful actions and no invented evidence in any of the 300 runs. The only path still untested on a live model is the Anthropic adapter's native one (`--provider anthropic`: real `tool_use`, cache control, refusal fallbacks), which needs an API key.

### Hand-written loop on Haiku 5.5

`sweeps/loop-haiku.yaml`: the hand-written loop, single agent, Haiku 5.5 at high effort, three runs per scenario, 75 runs, made on 2026-10-26 (`results/sweeps/loop-haiku-2026-10-26/`). One run timed out inside Claude Code and was retried, so no harness errors remain.

| configuration | pass rate (95% CI) | pass^3 | cost per solved | p50 wall | output tokens |
|---|---|---|---|---|---|
| hand-written loop, Haiku 5.5, high, single | 75% (59% to 89%) | 68% | $0.0141 | 53.3s | 8735 |
| hand-written loop, Opus 5.5, high, single (above) | 81% (67% to 93%) | 72% | $0.1149 | 36.8s | 2033 |
| Claude Code's own loop, Haiku 5.5, high (models table) | 77% (63% to 91%) | 68% | $0.0033 | 15.3s | |

- **Haiku in the hand-written loop is about as accurate as Opus in it, at about an eighth of the cost per solved incident.** The intervals overlap. It is slower and wordier: about four times the output tokens and 14 tool calls per run, against 8 for Opus.
- **Against Claude Code's own loop on the same model, accuracy is the same and cost is about 4x.** As with Opus, every turn here is a fresh completion that re-sends the transcript.
- Haiku never solved 009 (it blamed `cert-manager`), 014, 017 or 019. Opus in the same loop solved 009 every time. No harmful actions, no invented evidence and no refusals in the 75 runs.

Caveats: this is Haiku 5.5 only. The loop is the hand-written one, but each turn goes through headless Claude Code as a structured completion, so this is not the native `--provider anthropic` `tool_use` path, which is still untested on a live model. Runs are not independent (see above). Costs are list prices, not billed spend: Claude Code reports the token counts, and rootcause's price table turns them into dollars. Haiku 5.5 was missing from that table, which made the loop price it at the most expensive listed model. It was added for this sweep, and its rates reproduce Claude Code's own cost figures for the 2026-10-15 Haiku runs to within 0.02%.

### Mutation testing the brief

litmus's operators applied to the investigator brief: one broken copy per site (an instruction deleted, a rule inverted, a fact made wrong, the brief truncated or emptied), each run once over the suite at Opus 5.5 high: 12 mutants, 275 scored runs (`results/litmus/2026-10-15.md`). A mutant counts as killed when it fails a scenario the unmodified brief passed in all three baseline runs.

**0 of 12 mutants were killed: 11 survived and 1 was inconclusive.** The 11 survivors had pass rates between 72% and 84%, against 79% for the original. The inconclusive one is the emptied brief: all 25 of its runs errored, since the API rejects an empty system prompt. Read with the sweep results, the conclusion is about the suite more than the brief: no scenario depends on any single instruction, so the suite could not catch a regression in one. [The rule suite](#the-rule-suite) below is the attempt to fix that; the sweep and a mutation run of it are reported there: 2 of 14 mutants killed, neither for the reason it was built.

### The rule suite

Scenarios 026 to 033, added 2026-11-05, are the answer to the 0-of-12 result. Each is named for one rule of the investigator brief and built so that the rule decides the outcome. A 72-run sweep and a 112-run mutation run of them have been made (below). The mutation result is the one that matters, and it is mostly negative.

| scenario | rule | what the tempting wrong answer is |
|---|---|---|
| 026-rule-gather-evidence | gather evidence before concluding | the alert text blames auth's deploy, which landed seconds before the errors |
| 027-rule-symptoms-spread | the paging service is not always the cause | payments logs a panic of its own; ledger's database is out of connections |
| 028-rule-check-what-changed-when | check what changed, and when | a scary pool shrink landed after the errors began; an earlier client upgrade is the cause |
| 029-rule-cite-exact-ids | cite the exact ids the tools returned | the log text quotes look-alike ids for the guilty deploy |
| 030-rule-rollback-only-with-evidence | only request a rollback the evidence supports | the operator approves and a fresh deploy is innocent; the disk is full |
| 031-rule-finish-after-denial | finish the diagnosis if a rollback is denied | the right answer is a rollback, and the operator says no |
| 032-rule-no-incident | say no_incident when nothing is wrong | an alert threshold was cut tenfold; the service never changed |
| 033-rule-postmortems-are-hypotheses | past postmortems are hypotheses, not evidence | a postmortem matches the symptoms and blames a different cause |

What changed in the harness to support them:

- **Accepted services, written before any answer.** `accepted_services` in a scenario's truth lists other services a careful responder could defensibly name. It is set when the scenario is written and says so in the scenario file's header; only 027 (ledger beside ledger-db) and 032 (alerting beside checkout) have one. `--strict-service` on `rootcause eval` and `rootcause report` keeps the old single-service grade, so the 525-run rows read the same as before. 014 and 019 were not widened, and neither was 017: their models' answers are already in this README, so widening them now would be fitting the grader to the answers.
- **A held-out split.** `held_out` in a sweep file names scenarios never used to change the brief, the grader or an accepted set, and `rootcause report` prints in-sample and held-out pass rates in separate columns. In `sweeps/rule-suite.yaml` these are 027, 030 and 033, chosen before any run. This controls for tuning against what was seen. Every scenario still has the same author, so it says nothing about generalization.
- **A required retrieval step.** The truth of each rule scenario lists `search_postmortems` as a required tool; a run that was offered search and never called it fails. When search is not offered nothing is required, so the same scenarios work as a no-retrieval control. This makes search a measured step, but only because the search hint in the brief now says to search at least once before concluding, which is a different brief from the one used in the 2026-10-15 retrieval rows. 033 has a matching postmortem (PM-035) and a decoy (PM-034); the retrieval labels were re-scored with BM25 only (`results/retrieval/2026-11-05.md`: recall@5 0.98 on the alert plus first error line, 26 labels), and the embedding retrievers were not re-run.

What was checked without a model: the oracle answer passes every scenario, no answer or a wrong category fails it, and the oracle fails when it skipped a required search that was offered; the last-deploy baseline solves 4 of the 8 (028, 029, 031, 033) because their traps are readings of the brief, not of the timeline, so it passes 7 of 33 overall.

**The sweep** (`sweeps/rule-suite.yaml`, 72 runs on 2026-11-06 through a Claude subscription, `results/sweeps/rule-suite-2026-11-05/`; grader with accepted services, search offered and required, Claude Code's own loop as in the 525-run sweep, no harness errors; cost is list price, $2.63 in all):

| configuration | pass rate (95% CI) | in-sample (15 runs) | held-out (9 runs) | cost per solved | searched |
|---|---|---|---|---|---|
| Opus 5.5, high, BM25 | 100% (68% to 100%) | 100% | 100% | $0.0756 | 100% |
| Sonnet 5.5, high, BM25 | 100% (68% to 100%) | 100% | 100% | $0.0308 | 100% |
| Haiku 5.5, high, BM25 | 88% (71% to 100%) | 93% | 78% | $0.0036 | 100% |

- **It does not separate Opus from Sonnet.** Both passed all 24 runs, so the suite has a ceiling here. It separates Haiku only weakly: three failures in 24 runs, two on 030 and one on 032, all of them a wrong category with the right service and evidence, and the intervals overlap.
- **Search is now used.** Every run called `search_postmortems`, against 3 of 75 before. That is the effect of the brief telling the agent to search and the grader requiring it, so it measures instruction-following, and the sweep says nothing about whether search helps accuracy. Without a no-search configuration there is no comparison for that.
- **The baseline for mutation scoring is usable.** Opus at high effort passed every one of the eight scenarios in all three runs, so each can kill a mutant. The mutant run itself is what shows whether any does.
- **Caveats.** Nine runs per configuration on the held-out side is little. The three tiers were run at one setting each, and a 100% pass rate on scenarios the same author wrote against the brief the same author wrote is a weak signal that the scenarios are easy for strong models, not that the suite is solved.

Why a kill was possible at all, and what could still go wrong:

- 029 may be unkillable: the grader already rejects any cited id no tool returned, whatever the brief says, which is why the cite-the-ids mutants survived before.
- A mutant is only killed on a scenario the unmodified brief passes in every baseline run. If the baseline is unstable on a scenario, that scenario cannot kill anything.

**The mutation run** (`sweeps/litmus-brief-rule-suite-2026-11-05.yaml`, 14 mutants of the search-on brief, 8 scenarios, one run each, 112 runs on 2026-11-06, `results/litmus/2026-11-06-rule-suite.md`): **2 of 14 mutants killed, 11 survived, 1 inconclusive** (the emptied brief, which the API rejects, as before). The baseline was the Opus run above.

- **Both kills are the search requirement, not a rule.** Deleting the search-hint line fails all eight scenarios, and truncating the brief to its first five lines (which drops that line) fails seven. They fail because the grader requires a search the brief no longer asks for. That is a real regression the suite now catches, and the search hint changed in this same work, so it is the one rule the grader was made to enforce.
- **None of the rules the scenarios were built around was caught.** Deleting or inverting gather evidence, symptoms spread, cite exact ids, rollback only with evidence, and no_incident, or finishing in plain text, all survived, as did making two facts wrong. The scenarios were designed so that each rule decides its scenario. On one run per scenario per mutant, with a strong model, they did not. A model that is good at the task does the right thing without being told the rule.
- **So the original diagnosis is only half fixed.** The suite can now catch a brief that stops asking for search. It still cannot catch a regression in a single behavioural instruction, which is the 0-of-12 result's point. One run per scenario is also thin: a mutant that fails a scenario one time in three would usually survive.

## Limitations

What the results above do not show, in one place:

- **Small n.** The headline sweep has 25 incidents, three runs each. Runs cluster within an incident (about 30 effective runs per setting, see [Results](#results)), so the design detects only accuracy gaps above about 13 to 19 points between model tiers, 12 to 15 between single agent and orchestrator, and about 8 between the loop and LangGraph. A 10-point gap between Opus and Haiku would need about 84 incidents. "No detectable difference" here is a power limit, not a tie.
- **One author for everything.** The same person wrote the 30 synthetic scenarios, the 35-postmortem retrieval corpus, its relevance labels and the ground truth for the three recorded CI failures. That flatters lexical retrieval (see [Retrieval on its own](#retrieval-on-its-own)) and limits how far any pass rate generalizes. The held-out split in the rule suite guards against tuning, not against a shared author. For an externally sourced held-out set, see #18.
- **The grader moved after the results.** Accepted services were added after the 525-run sweep was read. Every result from the 2026-10-15 and 2026-10-26 sweeps is still graded strictly (`--strict-service`); only the 2026-11-06 rule-suite rows use accepted services, and of those scenarios only 027 and 032 have a set. The three scenarios whose answers were already seen (014, 017, 019) were not widened. The lenient Opus-Sonnet gap is post hoc and uncorrected (see above).
- **Twenty-one comparisons.** The models table invites 21 pairwise reads. After a Holm correction none differs; the one resampled interval that excludes zero (hybrid minus BM25) is that kind of false positive.
- **Mutation testing mostly failed.** No behavioural rule of the brief is caught by the suite yet (0 of 12, then 2 of 14 through the search requirement only).
- **What has not run.** The Anthropic adapter's native `tool_use` path has only been tested against a mocked transport; every live result went through Claude Code. `deploy/azure/` and the `deploy-azure` workflow have never been dispatched, so no hosted endpoint exists. Costs are list-price estimates from recorded token counts, not billed spend.

## Architecture

```mermaid
flowchart LR
  subgraph interfaces
    ACT["GitHub Action<br/>action.yml"] --> CLI["CLI<br/>interfaces/cli.py"]
    MCPS["MCP server for coding agents<br/>interfaces/mcp_server.py"]
  end
  CLI --> COMP["composition.py<br/>the only wiring"]
  MCPS --> COMP
  subgraph application
    LOOP["AgentLoop, or its LangGraph port<br/>application/agent.py"]
    EXEC["ToolExecutor + approval gate<br/>application/executor.py"]
    GRADE["grader<br/>application/evals.py"]
  end
  COMP --> LOOP
  COMP --> CC["Claude Code's own loop<br/>adapters/claude_code.py"]
  COMP --> GRADE
  LOOP --> EXEC
  EXEC -- "MCP client" --> TOOLS["telemetry MCP tools<br/>adapters/mcp_telemetry.py"]
  CC -- "MCP, allow-listed tools" --> TOOLS
  LOOP -- "turns" --> LOG[("append-only SQLite log<br/>adapters/sqlite_events.py")]
  EXEC -- "intent and result rows" --> LOG
  LOOP -. spans .-> OTEL["OpenTelemetry<br/>adapters/otel.py"]
  EXEC -. spans .-> OTEL
```

The Action is a composite step that calls `rootcause eval`. The LangGraph engine (`adapters/langgraph_engine.py`) replaces only the control flow and uses the same `ToolExecutor`. Claude Code's own loop skips the hand-written loop and the executor: its gate is `--allowedTools`. `domain/` (telemetry, scenarios, answers, postmortems) sits under all of it with no framework imports. Design decisions and the evidence for each are in [docs/decisions.md](docs/decisions.md).

The layering is enforced, not described: `import-linter` contracts in `pyproject.toml` run in CI, and `tests/test_contracts.py` checks the rules import-linter can't express. `docs/AUDIT.md` lists the SSOT, SOLID and layering problems found so far, how each was fixed, and which changes were written test-first.

### Optional extras

The core install has no ML or telemetry dependencies. Add what you need:

| extra | adds | install |
|---|---|---|
| `rag` | static and transformer embeddings for `--retrieval static\|dense\|hybrid` (BM25 needs nothing) | `uv sync --extra rag` |
| `otel` | OpenTelemetry SDK and OTLP exporter for `--trace` | `uv sync --extra otel` |
| `langgraph` | the LangGraph version of the investigation, `--provider langgraph` | `uv sync --extra langgraph` |

A CI job installs the core alone and runs the tests, the baseline and BM25 retrieval, and the import contracts keep numpy, the embedding libraries and OpenTelemetry out of the domain and application layers. Asking for a mode whose extra is missing fails with the install command.

## Scenarios

`scenarios/001` to `007`, `009` to `022`, `025` and `026` to `033` are written as code in `scripts/author_scenarios.py`, each with a docstring saying what makes it hard. A test regenerates them and fails if the committed fixtures drift, and another fails if the baseline solves more than half. A runtime flag flip or an operator's cache flush shows up as a log line, the way a flag service or a runbook audit log would record it, so neither needed a new tool.

`scenarios/008`, `023` and `024` are real failed runs from [sous](https://github.com/OrenSegal/sous): the raw `gh` output is kept in `raw/`, the fixtures are derived from it, and the ground truth was set by reading the logs and diffs, with the reasoning in each `scenario.yaml` header. In 024 the head commit is a decoy and only the earlier guilty commit counts as evidence.

## Use it from a coding agent

```bash
claude mcp add rootcause -- uv run --directory "$PWD" rootcause mcp
claude mcp add incident-007 -- uv run --directory "$PWD" rootcause telemetry-mcp scenarios/007-inventory-timeout-cascade
```

The first exposes `list_scenarios`, `investigate_incident` and `run_evals`. The second hands a coding agent the raw telemetry tools, so the agent itself can be scored as the investigator. It is read-only by default, because no operator or approval policy runs on that path. `--allow-rollback` adds the rollback tool, and then the coding agent's own permission settings are the only gate. `--provider claude-code` passes that flag and gates with `--allowedTools`.

## Use it in CI

The eval gate, as a reusable action:

```yaml
- uses: OrenSegal/rootcause@v0.2.0
  with:
    provider: heuristic
    min-pass-rate: "0.05"
```

Triage of every failed CI run, in Actions: `.github/workflows/triage.yml` runs on `workflow_run` when the `ci` workflow fails, checks out the default branch (never the code under test), and writes a diagnosis to the job summary. It needs only `actions: read` and `contents: read`. Tried on a deliberately failing pull request: it named the commit that broke the build. It runs the heuristic provider, which blames the last commit, so that shows the plumbing works, not that the diagnosis is sound.

The same from a terminal, read-only (it fetches with `gh` and writes a comment body; it never posts):

```bash
rootcause ci-triage --repo owner/name --run 123456789 --provider anthropic --comment-md comment.md
rootcause ci-triage --repo owner/name --run 123456789 --record scenarios/   # keep it as a new scenario
```

Example comment bodies, produced offline from recorded runs by the heuristic provider: [one it gets right](docs/examples/ci-triage-023-sous-readme-table.md) and [one where it falls for a planted decoy](docs/examples/ci-triage-008-sous-shellcheck.md).

## Not done yet

- A human in the approval loop. The only policy is a static allow or deny.
- A resume command. Crash recovery is a library call with no CLI: the log doesn't record a run's mode, retrieval or approval, which a command would need to rebuild it.
- A live-model regression gate. The `--min-pass-rate 0.05` check in CI is a smoke test on the scripted provider.
- The Anthropic adapter on a live model: native `tool_use`, cache control and refusal fallbacks are tested against a mocked transport only. Every live result here reaches the model through Claude Code. The sweep file format already takes `provider: anthropic`.
- Scenarios that do kill a mutant of a behavioural rule. The rule suite killed 2 of 14, both through the search requirement; the rule-specific designs did not work on one run each. Next would be harder traps, several runs per mutant, and a weaker model as the baseline (Haiku failed 3 of 24 here, so a stable baseline is the constraint).
- Telemetry recorded from a running service stack. The CI adapter covers real recorded data for now.
- A held-out set by a different author. The held-out split marks scenarios not used for tuning, but the scenarios, the corpus and the labels share an author, which flatters lexical retrieval and limits how far any result here generalizes.
