# Did the change break the build?

<!-- DRAFT. Every held-out number is a TODO until results/sweeps/ci-test-2026-11-23/ is
committed. Do not fill a TODO from the dev split. -->

When a pull request's CI run fails, the first question is whether the change under test caused it. If it did, the author fixes the code. If it did not, someone re-runs the job, pins a dependency or reports a flaky test. Getting this wrong in either direction wastes time: a developer chases a failure their change did not cause, or a real break gets re-run until it passes by luck.

This write-up describes a benchmark of real CI failures that asks that question, what it takes to grade it honestly, and how a cheap triage pipeline compares with the strongest model on its own.

## The benchmark

168 failed GitHub Actions runs on pull requests from 55 public Python repositories. Each case is one failed run with its job tree, the failed jobs' log lines and the commits under test with the files each changed. The truth is one of two classes:

- **change**: a commit under test caused the failure. The truth names that commit. 108 cases.
- **not_change**: something else did, such as a flaky test, a network error, a runner fault or an upstream release. 60 cases.

Cases come from two mining passes. The first took red-to-green pairs on pull-request branches and proposed a label from what the fix touched. The second took runs whose first attempt failed and whose re-run of the same commit passed. Every kept case was reviewed against its failed log. The data card in [`benchmarks/ci/README.md`](../benchmarks/ci/README.md) lists every drop and exclusion with its reason.

The split is by repository. Dev has 51 cases from 16 repositories. The held-out test set has 117 cases from 39 other repositories. Everything below that was tuned, was tuned on dev.

## Grading

An answer passes when it gets the class right and, for a change case, cites the guilty commit. A not_change answer passes without a commit. The report gives accuracy on each class and their mean (balanced accuracy), because the classes are unequal and a one-sided answer can score well on raw accuracy.

Two baselines show why. Blaming the head commit names the guilty commit in 94% of change cases and fails every not_change case. The second baseline uses a leak in the data: not_change cases more often have several commits under test. Answering "not caused by the change" whenever there is more than one commit, and blaming the head commit otherwise, gets 75% raw accuracy on test without reading a log. A model has to beat that, and every comparison is also read on single-commit cases, where the leak gives nothing.

## Comparing two configurations

`rootcause diff A B` answers one question: did B beat A, or is the gap noise? It pairs the two result sets by case, so each case is compared with itself, and reports the paired gap with a resampled interval, a sign test on the cases where the two disagree, and the minimum detectable effect at this sample size. With `--fail-on-regression` it exits non-zero when A is better, so it can gate a pull request that changes a prompt or a model.

Pairing matters at this size. Two configurations can each have a wide interval while their paired gap is clear, because most cases are solved by both or neither and only the disagreements carry information.

## A triage ladder

Most CI failures do not need the strongest model. The ladder sends each case down three rungs:

1. **Rules.** Four deterministic rules answer only when they are sure: every failed job shows a known environment error; every commit changes only documentation; the failing lines name files exactly one commit changed; or a minority of one matrix failed and the failing lines name no changed file. Otherwise they abstain. They cost nothing.
2. **A cheap model** answers what the rules leave.
3. **A strong model** answers only when a lint flags the cheap answer as likely wrong.

The lints are free checks on an answer: a change answer that cites no commit; a blamed commit that changed no file the failing lines name; no log query that could return a failed job's lines; low stated confidence; a not_change answer while one step fails in every job of a matrix; and disagreement with a rule that answered. Each lint was scored on dev by how often it caught a wrong answer. Only one was kept for escalation: a change answer that cites no commit.

`rootcause ladder` replays the ladder from a finished sweep. It calls no model: repeat i of the cheap model pairs with repeat i of the strong model, an escalated case costs both runs, and the output feeds straight into `rootcause diff`.

## What was registered before the held-out run

The held-out sweep config, [`sweeps/ci-test.yaml`](../sweeps/ci-test.yaml), was committed before any model ran on the test cases. It fixes the grade, the brief, the rules, the escalating lint and the comparisons to read:

1. each model against the head-commit baseline, overall and on not_change;
2. Haiku with and without the brief line that asks for the guilty commit's id;
3. Haiku against Opus, both with that line;
4. the ladder against Opus alone.

Each is also read on single-commit cases. Six configurations, three repeats, 117 cases: 2,106 runs.

## Results

TODO: fill from `results/sweeps/ci-test-2026-11-23/` once it is committed. One paragraph per registered comparison, cause before claim, with the `rootcause diff` verdict and the single-commit reading.

| configuration | change | not_change | balanced | cost per case |
|---|---|---|---|---|
| head commit | TODO | TODO | TODO | $0 |
| commit-count rule | TODO | TODO | TODO | $0 |
| rules (answered cases only) | TODO | TODO | TODO | $0 |
| Haiku 5.5 | TODO | TODO | TODO | TODO |
| Haiku 5.5, cite line | TODO | TODO | TODO | TODO |
| Sonnet 5.5, cite line | TODO | TODO | TODO | TODO |
| Opus 5.5, cite line | TODO | TODO | TODO | TODO |
| ladder | TODO | TODO | TODO | TODO |

- Rules coverage on test: TODO cases answered of 117, TODO right.
- Ladder escalations: TODO of TODO cheap answers.
- Ladder against Opus alone: TODO (diff verdict, gap, interval, cost ratio).
- The brief line on unseen repositories: TODO.
- Test cases every model gets wrong: TODO. Listed for review, not relabelled.

## Limits

- **Labels were reviewed, not blind.** The same author's rules proposed every rule label, and the review judged the proposed label. The planned blind audit with an agreement figure was not run.
- **One person's rules proposed the labels.** Their blind spots are the benchmark's blind spots.
- **not_change is mostly one kind.** 37 of 60 not_change cases are re-run-passed failures: flaky tests, network and download errors, runner faults. Upstream releases and base-branch breaks are a handful of cases.
- **The commit count leaks the class.** not_change cases more often have several commits under test. Each comparison is also read on single-commit cases for this reason.
- **One rule's precision is inflated by how cases were mined.** The `environment` rule uses the same error patterns the miner used to propose not_change labels, so on rule-labelled cases it partly agrees with itself.
- **The rules and the escalating lint were tuned on dev.** The test split was not used to choose or change them, but they were chosen by looking at dev answers, so dev numbers for them are optimistic and are not reported as results.
- **Small n.** 38 not_change cases in test give a per-class interval about 30 points wide.
