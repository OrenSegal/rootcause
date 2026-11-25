# Changelog

## 0.3.0

RESULTS: to be filled from the held-out sweep

- A benchmark of real CI failures in `benchmarks/ci/`: 168 failed pull-request runs from 55 public Python repositories, each labelled as caused by the change under test (with the guilty commit) or not. Split by repository into dev (51 cases) and a held-out test set (117 cases). How the cases were mined and reviewed, and the review's limits, are in `benchmarks/ci/README.md`.
- Class grading for CI cases: an answer is judged on change against not change, and on the commit for a change. A `no_incident` answer counts as not caused by the change.
- `rootcause diff A B`: did B beat A, or is the gap noise. Pairs two result sets by case and reports the paired gap with a resampled interval, a sign test and the minimum detectable effect. Reads JSONL files or sweep configs; `--intersect`, `--cases`, `--class` and `--fail-on-regression` for CI.
- A `rules` provider: deterministic CI triage that answers only when a rule fires (environment error, docs-only change, one commit named by the failing lines, a minority of one matrix failing) and otherwise abstains. Free, no API key.
- Lints that flag a model's CI answer as likely wrong, and `rootcause ladder`, which replays a finished sweep as rules first, a cheap model where they abstain and a strong model where a lint flags the cheap answer. No model calls.
- `rootcause ci-triage` writes a short verdict: what failed, whether the change under test likely caused it and which commit, the log lines behind it, and a footer naming the provider and the rule. With the rules provider, a run no rule covers says so. Text from the log, a commit or a model can't close a code block or mention anyone in a comment.
- Corrected two one-line notes in `benchmarks/ci/verdicts.csv` (labels unchanged): the sentry-python pair crashed in coverage's data file, and the cibuildwheel pair hit a Windows permission error moving a downloaded wheel into a cache. `labels.csv` records each repository's licence as read from its licence file, where GitHub reported none.
- `docs/ci-benchmark.md`: the CI benchmark write-up. The README leads with it.
- `ci-triage` no longer crashes when `gh` finds no pull request for a run; the tested commit is then the only suspect. Among pull requests that share a branch name, it takes the one at the tested commit.
- Two new actions, documented in `docs/actions.md`. `OrenSegal/rootcause/triage` triages a failed run from a `workflow_run` workflow, read-only by default, with an opt-in pull-request comment. `OrenSegal/rootcause/diff` runs `rootcause diff` on two result files and fails the job on a regression. The root action (the eval gate) is unchanged.
- Releases publish to PyPI from `.github/workflows/release.yml` through trusted publishing.
- Claude Code stream lines over 64 KB are read whole; a 200-line CI log result used to crash the run.
- README cut to the findings, how grading works and how the agent is built; the full results and limitations moved verbatim to `docs/results.md`. Removed the never-dispatched Azure deploy (`deploy/azure/`, its workflow) and the week-1 `docs/AUDIT.md`; its one standing rule is decision 6 in `docs/decisions.md`.
- The distribution is named `rootcause-agent` (the PyPI name `rootcause` belongs to another project). The import package and the `rootcause` command are unchanged; `[tool.uv.build-backend] module-name` tells `uv_build` so. A built wheel is `rootcause_agent-<version>` with the `rootcause/` package inside.
- An externally sourced held-out set: eight incidents adapted from public postmortems (`scenarios-external/`, `scripts/author_external.py`), truths pre-registered in f5417b1 before any run. Haiku 5.5 passes 50% (cluster-bootstrap CI 21% to 79%) against 77% on the main suite; 24 Haiku runs (48 with the heuristic) in `results/sweeps/external-heldout-2026-11-07/`.
- `scripts/resample_gaps.py` prints each model setting's intraclass correlation, effective number of runs and interval width against Wilson, and gives loop minus LangGraph its minimum detectable effect. README gains a Limitations section and a mermaid component diagram; `docs/decisions.md` records five design decisions with evidence. The Azure deploy README says it has never been run.
- Sweep intervals resample scenarios (cluster bootstrap, seeded) instead of a Wilson interval over runs, which treated repeats of one scenario as independent; committed reports regenerated, only the intervals changed. `scripts/resample_gaps.py` adds strict-grader paired gaps with sd, minimum detectable effect, sign tests and Holm correction, and the README now states accuracy differences as unresolved below about 13 to 19 points.
- Eight rule scenarios (026 to 033), accepted services in the truth with `--strict-service` for the old grade, a held-out split in sweep files and the report, and a required search step. A 72-run sweep of them is in `results/sweeps/rule-suite-2026-11-05/`; mutation-testing the retrieval brief with them killed 2 of 14 mutants, both through the search requirement (`results/litmus/2026-11-06-rule-suite.md`).

## 0.2.0 (2026-11-02)

- Fixed the GitHub Action, the approval gate and resume (d3c2545). The telemetry MCP server takes rollback as an explicit option and is read-only by default over stdio; the loop gates on its own list of read-only tools instead of server hints.
- Added a Haiku 5.5 sweep of the hand-written loop, 75 runs (`results/sweeps/loop-haiku-2026-10-26/`).
- Rewrote README claims about the grader and sweep results to match the committed result files.

## 0.1.0

- First tagged version: agent loop, MCP telemetry tools, approval gate, SQLite event log, orchestrator with workers, retrieval over postmortems, OpenTelemetry traces, 25 scenarios, sweeps and reports, GitHub Action.
