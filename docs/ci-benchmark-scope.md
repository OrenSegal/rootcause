# Scope: a benchmark of real CI failures

Status: milestone 1 done on 2026-11-21 (see the two result sections at the end). Next: validate the rules on repositories they have not seen, then milestone 2.

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

## Milestone 1 result (2026-11-21)

`scripts/mine_ci.py` on five repositories (pallets/flask, pytest-dev/pytest, pydantic/pydantic, psf/black, fastapi/fastapi), push runs on the default branch since 2026-08-25, at most 10 breaks per repository: **31 breaks, 8 kept, 23 dropped.** Every kept case was checked by hand against the failed log and both diffs.

**The first run had a rule bug.** Most "guilty commit" labels rested on the fix and the suspect both editing `CHANGES.md`, `AUTHORS` or release notes. Changelog-type files are now ignored when matching (`is_note`), with a test.

**After that fix, the labels are still mostly wrong.** Of the 8 kept cases:

| case | automatic label | by hand |
|---|---|---|
| fastapi 37688429128, 43475880790, 44310583790 | infrastructure (rerun passed) | plausible, but the only error line is the aggregator job's exit code |
| black 40142167378, 40259878945 | bad code, guilty commit named | wrong: one Windows ARM job of 31 failed a cache test with a file error, a flaky test |
| black 41256375361, 41256375403 | bad code, a docs-typo commit | wrong: the runner image moved to Ubuntu 26.04, which has no Python 3.12.10; infrastructure |
| pydantic 37828953430 | bad code, an action bump | wrong: `socket hang up` while building one wheel of 111 jobs; a network flake |

21 of the 23 drops are "no tested commit touched the files the fix changed": the next green commit was unrelated, so the failure most likely went away by itself.

**What this says.** On a protected default branch, failures are mostly flakes and environment changes, and "the next green commit is the fix" does not hold. The rules as written would have produced a benchmark with wrong answers. This is the stop condition in milestone 1.

**Proposed changes before milestone 2:**

1. **Mine pull requests, not the default branch.** On a PR, a red commit followed by a green commit on the same branch is usually the author fixing what they broke, so the fix signal is far stronger. Main-branch breaks become a separate infrastructure-and-flake set.
2. **Flake rules ahead of the fix rule.** One failing job out of a matrix where the same tests passed elsewhere, or the same failing test name recurring across unrelated breaks, labels the case flaky.
3. **Environment rule.** Known error shapes (`version ... was not found`, `socket hang up`, rate limits, runner timeouts) label infrastructure with no guilty commit.
4. **Step names.** `gh run view --log-failed` now prints `UNKNOWN STEP` for every line, so logs lose their step. Take step names from the run's job and step timings instead.

The raw snapshots from this run are kept locally in `scenarios-ci/` (not committed: the labels are wrong, but the logs expire after 90 days).

## Milestone 1, second pass: pull requests (2026-11-21)

The miner now reads pull-request runs, with each PR branch as its own timeline (`--event pull_request`, the default). Same five repositories and window, at most 12 breaks each.

Rules added, each prompted by a case that was mislabelled, each with a test:

- **Environment errors come before the fix rule** (`ENVIRONMENT`): a missing Python build on the runner, `socket hang up`, `ENOTFOUND`, a failed artifact upload and similar label infrastructure with no guilty commit.
- **A lone failing matrix job is dropped**, not labelled. If one job failed while two or more jobs of the same matrix passed, it is a flake or a platform bug, and the rules cannot tell which. Gate jobs (`check`, `*-alls-green`) are ignored when counting.
- **"Rerun passed" now checks the first attempt's jobs.** A first-time contributor's run waits for approval and then passes, which looked like a flaky failure. GitHub also overwrites an attempt's own conclusion when its failed jobs are re-run, so the check counts failed jobs in that attempt.
- **Force-pushes are dropped.** If the red commit is not an ancestor of the green one, the diff between them is not a fix.
- **Step names are recovered** from the run's step timings when `gh` prints `UNKNOWN STEP`.

**Result: 49 breaks, 12 kept.** Each was checked by hand against the failed log and both diffs:

| verdict | cases |
|---|---|
| right | fastapi 43088004454, 43612164925, 44138170508 (new code dropped coverage below the gate; the fix added tests); fastapi 43418818964 (a workflow lockfile commit broke pre-commit; config); pydantic 44617804639 (a new streaming test failed on four jobs; the fix changed that test); pydantic 44623649807 (artifact upload failed on DNS; infrastructure); pydantic 44749339872, 44767659644 and pytest 42785732184, 43162181446 (failed, then passed on a re-run of the same commit; flaky) |
| right commit, arguable category | fastapi 44694893735: a docs commit failed the formatter. Labelled `bad_code_deploy`; the six categories have no place for a lint failure |
| uncertain | flask 33236247623: one change split into four commits, and the label blames the commit that edited the test; the source edits are equally plausible |

Two cases from the first PR pass that were wrong (pytest 43079052021, an unrelated PyPy refcount test; pydantic 44609527725, one Windows PyPy job) are now dropped by the matrix rule.

**Caveats.** The rules were fixed on these same cases, so 11 of 12 is dev-set accuracy, not a measurement. The next batch must come from repositories the rules have not seen, and its hand check is the real test. Yield is about 1 in 4 breaks, so 150 cases needs roughly 600 breaks: about 25 repositories at this cap, or fewer with a higher one. The category set needs a decision for lint and coverage failures before milestone 2.

The snapshots are in `scenarios-ci/pr/` (13 MB) and `scenarios-ci/main/`, not committed.
