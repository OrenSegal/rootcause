# Scope: a benchmark of real CI failures

Status: proposal, 2026-11-21. Nothing here is built yet.

## Why

The current suite has three problems, all listed in the README's limitations. It does not separate models (77% to 84% across Haiku to Opus). It is too small to detect gaps under about 13 to 19 points. And one person wrote every scenario. Real failed CI runs from public repositories fix all three. Thousands of other people wrote them, there are plenty of them, and the commit that turned the build green again is a ground truth nobody has to invent.

It also makes the tool useful. `rootcause ci-triage` already turns a failed Actions run into telemetry the agent can investigate, and `--record` already saves one as a scenario. It has only been graded on three runs from one repository.

## The question it answers

Given a failed CI run and the commits under test, can an agent say **why it failed** and **which commit broke it**, more accurately than a smart grep, and how much does each correct answer cost?

## Data

**Source.** Public GitHub repositories with an OSI licence and Actions history, chosen from a fixed list before mining (e.g. the top 50 Python and TypeScript repos by stars that run tests in Actions). Logs are already public; GitHub masks secrets in them. Each case keeps the raw `gh` output in `raw/`, as scenarios 008, 023 and 024 do, with repo, run URL and licence in the `scenario.yaml` header.

**Mining a case.** A case is a red-to-green pair on one workflow and branch:

1. a failed run on commit A;
2. the next successful run of the same workflow on that branch, on commit B;
3. the commits under test for A: the commits since the last green run, from the compare API.

**Time window.** Only runs after the newest model's training cutoff, so no model can have read the fix. Mine now: GitHub deletes run logs after 90 days by default, and the snapshot in `raw/` is what makes cases permanent.

**Size.** 150 cases. Scaling the current design's minimum detectable effect by the square root of n, 150 incidents detects gaps of roughly 5 to 8 points, against 13 to 19 at 25. Split 30 dev and 120 test, with the test truths committed and pushed before any model or baseline runs on them, as the external set was.

**Budget.** About 10 read-only API calls per case, so 150 cases fit well inside the authenticated rate limit (5,000 an hour).

## Labels, derived automatically

Each case gets a category and a guilty commit (or none). The rules run in order, and the first that fires sets the label:

| rule | category | guilty commit |
|---|---|---|
| a rerun of the same commit passed | `infrastructure` (flaky or runner) | none |
| the fix touches only lockfiles or version pins, and no commit under test touched them | `dependency_failure` (upstream broke) | none |
| the fix touches only workflow or config files | `config_change` | the commit under test that last touched those files, if exactly one |
| the fix touches source or tests, and exactly one commit under test touched the same files | `bad_code_deploy` | that commit |
| anything else | dropped | |

Dropped cases are counted, and the drop rate per rule is reported, because a benchmark that keeps only the easy-to-label cases is biased toward easy cases.

**Human audit.** 30 cases drawn at random are labelled by hand, without seeing the automatic label, from the log and both diffs. Agreement is reported as Cohen's kappa. If kappa is below 0.7, fix the rules before running any model.

## Grading

The grader stays as it is, with one change. In CI scenarios the service is always the repository, so the service check passes trivially. The report shows two numbers per configuration instead of one pass rate:

- **category accuracy**;
- **guilty-commit accuracy**: the cited evidence includes the guilty commit, or cites no commit when there is none.

A run passes when both are right. Intervals, pass^k, cost per solved and paired gaps come from the existing sweep and report code.

**Optional, phase 2: fix localization.** Score whether the agent names the files the fix touched (recall@3). This is the number a developer would care about most, but it needs a new answer field, so it waits until the core benchmark works.

## Baselines

A benchmark is only interesting if it beats cheap heuristics. Two run before any model:

1. **Last commit.** The existing heuristic: blame the newest commit under test.
2. **Grep.** Blame the commit under test whose changed files are named in the failing log lines (file paths, module names, test names). This is the one a model has to beat. If the grep baseline is within the interval of the models, the benchmark has measured log-reading, not reasoning.

## Runs

Haiku 5.5, Sonnet 5.5 and Opus 5.5 at high effort, Claude Code's own loop, three runs per case on the 120 test cases: 1,080 runs. At the main sweep's mean cost per run (Haiku $0.0025, Sonnet $0.026, Opus $0.064) that is about $35 at list price. CI logs are longer than the synthetic ones, so budget up to $150. Run it once through the hand-written loop on Haiku as well, and once on the native `anthropic` provider, which has never run live.

Then mutation-test the brief against the new suite. The headline result to look for: does a suite of real failures catch a brief regression that the synthetic suite missed (0 of 12)?

## Code changes

Small, because most of the path exists:

- `adapters/github_actions.py`: for `push` runs, list the commits under test from the compare API (last green to failed) instead of only the head commit. Record `attempt` so the flaky rule can fire.
- `scripts/mine_ci.py` (new): walk the repo list, find red-to-green pairs in the window, snapshot `raw/` (failed run, fix commit diff, rerun result), apply the label rules, write `scenarios-ci/<id>/` with the reason for each label in the header.
- `adapters/heuristic.py`: add the grep baseline as a second provider.
- `application/evals.py` and the sweep report: category and guilty-commit accuracy as separate columns for recorded CI scenarios.
- Tests: label rules on fixed raw snapshots, one per rule including the drop case; the compare-API path through the existing fake `gh` runner.

## Milestones

1. Miner and label rules on 5 repos, by hand-checking every case. Stop here if most cases drop.
2. Full mine of 150 cases, plus the 30-case human audit and kappa.
3. Both baselines, then pre-register: commit and push the test truths.
4. The model sweep and report.
5. Mutation run, then a short write-up with the chart: what separated models on real failures, and what the suites catch.

## Risks

- **Labels too noisy.** The audit and kappa gate catch this before any spend.
- **Most cases are flaky or upstream.** That is still a finding, and those cases have no guilty commit, which tests whether the agent will say "none".
- **Logs too long.** `query_logs` caps at 200 lines per call, so long logs cost more calls rather than failing. Watch cost per run in milestone 1.
- **The grep baseline wins.** Then the write-up says so. It would be a useful result for anyone building CI triage.
- **Repos change licence or delete history.** The `raw/` snapshot keeps every case reproducible, and each header records the licence at mining time.
