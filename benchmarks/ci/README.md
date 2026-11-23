# CI failure benchmark

168 failed GitHub Actions runs on pull requests from 55 public Python repositories. For each one the question is: did the change under test cause this failure, and if so which commit, with evidence? Or did something else cause it (a flaky test, the runner image, the network, an upstream release)?

## What a case is

A case is one failed workflow run, recorded as a scenario directory that `rootcause eval` loads:

- `scenario.yaml`: the alert (workflow, branch, head commit, failed job and step), the pull-request title, and the truth.
- `logs.jsonl.gz`: the failed jobs' log lines (gzipped; the loader reads it directly).
- `traces.json`: the run, its jobs and steps as spans.
- `deploys.json`: the commits under test, with the files each one changed.
- `metrics.json`: empty.

The truth is either `bad_code_deploy` or `config_change` citing the guilty commit (class `change`), or `infrastructure` (class `not_change`). A `change` answer must cite the guilty commit. A `not_change` answer must give a category of that class and cite real evidence; it may mention a commit. The service is always the repository name.

`provenance/<case>/` keeps the mined `scenario.yaml` with its comment header (run URL, fix run, label rule), and `run.json.gz`, `commits.json` and `fix.json` from the mining. No telemetry tool reads this directory. The raw failed log is not kept; the fixtures were built from it.

```bash
uv run rootcause eval benchmarks/ci/dev --provider heuristic --min-pass-rate 0
```

## Where it came from

Two mining passes with `scripts/mine_ci.py`, both on pull-request runs since 2026-08-25.

1. **Red to green.** Red-to-green pairs on pull-request branches in the repositories registered for the first PR pass and validation rounds 1 to 4 of `docs/ci-benchmark-scope.md`. Automatic rules proposed a label for each kept break. Each proposed label was reviewed against the failed log and both diffs, and the scope document records every verdict as right, arguable, uncertain or wrong.
2. **Re-run passed** (`--rerun-only`). Pull-request runs whose first attempt failed and a re-run of the same run, on the same commit, passed. These come from 31 repositories chosen before mining, none of which has a case from the first pass in this benchmark. Every kept case was reviewed against its failed log before it was included.

`verdicts.csv` holds one row per mined case: the verdict, the label source, the label where the review states it, and a one-line note for the reviewed and re-run cases. `scripts/freeze_ci_benchmark.py` reads it and the mined cases (kept locally in `scenarios-ci/`, not committed) and writes this directory. Running it twice gives identical files.

## Labels

Each case has one of three label sources (`label_source` in `labels.csv` and `verdicts.csv`):

| source | what it means | change | not_change |
|---|---|---|---|
| `rule` | the rules proposed the label and the review recorded it as right; the script checks the stored label agrees with the table | 106 | 12 |
| `review` | the review recorded the rule's label as wrong, and the scope document states the right one; the truth is rebuilt from it | 2 | 11 |
| `rerun` | the first attempt failed and a re-run of the same run passed; the review confirmed the failure looks transient | 0 | 37 |

For a `review` case labelled `not_change`, the truth cites the first five error-level log lines, as the miner does for its own infrastructure labels. Every `not_change` case is filed as `infrastructure`. The truth says `grading: class`: the grader checks change against not change and the commit, not the exact category.

The review was not blind. The same author's rules proposed every rule label, and the review judged the proposed label. The `review` labels come from the same review. The 30-case blind audit with Cohen's kappa planned in the scope document was not run, and no agreement figure exists.

## Counts

| | change | not_change | cases | repositories |
|---|---|---|---|---|
| dev | 29 | 22 | 51 | 16 |
| test | 79 | 38 | 117 | 39 |
| all | 108 | 60 | 168 | 55 |

Categories: 101 `bad_code_deploy`, 7 `config_change`, 60 `infrastructure`.

| | rule | review | rerun |
|---|---|---|---|
| dev | 27 change, 5 not_change | 2 change, 4 not_change | 13 not_change |
| test | 79 change, 7 not_change | 7 not_change | 24 not_change |

Dev is for tuning an agent; test is held out. The split is by repository, so no repository is in both. Repositories were shuffled with seed 20261123; in that order each went to dev if dev then stayed within 2 cases of its target for both classes, a third of `not_change` and a quarter of `change`. The first shuffle that ended within 2 cases of both targets was used. Dev holds 22 of 60 `not_change` cases (37%) and 29 of 108 `change` cases (27%).

| repository | split | change | not_change |
|---|---|---|---|
| aio-libs/multidict | dev | 7 |  |
| Delgan/loguru | dev |  | 1 |
| geopandas/geopandas | dev |  | 3 |
| getsentry/sentry-python | dev |  | 2 |
| holoviz/panel | dev | 3 | 3 |
| ipython/ipython | dev | 6 |  |
| matplotlib/matplotlib | dev | 4 |  |
| pallets/click | dev | 3 |  |
| pypa/build | dev | 4 |  |
| pypa/pipx | dev | 2 |  |
| pytest-dev/pytest | dev |  | 2 |
| pytest-dev/pytest-xdist | dev |  | 3 |
| python-poetry/poetry-core | dev |  | 2 |
| streamlink/streamlink | dev |  | 3 |
| sympy/sympy | dev |  | 1 |
| vega/altair | dev |  | 2 |
| agronholm/anyio | test | 2 | 1 |
| aio-libs/propcache | test | 1 |  |
| aio-libs/yarl | test | 1 | 1 |
| beetbox/beets | test |  | 2 |
| bokeh/bokeh | test | 3 |  |
| dask/dask | test | 5 |  |
| dask/distributed | test | 1 |  |
| django/django | test | 1 |  |
| encode/django-rest-framework | test | 3 |  |
| encode/starlette | test | 5 |  |
| encode/uvicorn | test | 6 |  |
| fastapi/fastapi | test | 4 |  |
| huggingface/huggingface_hub | test |  | 1 |
| ipython/ipykernel | test |  | 3 |
| joblib/joblib | test |  | 4 |
| jupyter-server/jupyter_server | test |  | 2 |
| nedbat/coveragepy | test | 3 |  |
| networkx/networkx | test | 7 |  |
| numpy/numpy | test | 1 |  |
| pallets/jinja | test | 6 |  |
| pandas-dev/pandas | test | 4 |  |
| pyca/cryptography | test | 1 |  |
| pydantic/pydantic | test | 1 | 3 |
| pydantic/pydantic-settings | test |  | 1 |
| pydata/xarray | test | 6 |  |
| pypa/cibuildwheel | test |  | 4 |
| pypa/pip | test |  | 1 |
| pypa/pip-tools | test |  | 2 |
| pytest-dev/pytest-asyncio | test | 5 |  |
| python-attrs/attrs | test | 3 |  |
| python-poetry/poetry | test | 1 | 1 |
| python/mypy | test | 1 |  |
| pyvista/pyvista | test |  | 1 |
| scikit-image/scikit-image | test | 1 |  |
| scikit-learn/scikit-learn | test | 1 | 1 |
| scrapy/scrapy | test | 1 | 4 |
| sphinx-doc/sphinx | test |  | 2 |
| tornadoweb/tornado | test |  | 4 |
| urllib3/urllib3 | test | 5 |  |

## The re-run pass

**Repositories, chosen before mining.** Public, Python, with active pull requests, an OSI licence as GitHub reports it, and not in the first pass: vega/altair, plotly/plotly.py, huggingface/datasets, huggingface/huggingface_hub, fastapi/sqlmodel, fastapi/typer, python-attrs/cattrs, Delgan/loguru, more-itertools/more-itertools, pypa/pip-tools, python-poetry/poetry-core, arrow-py/arrow, mwaskom/seaborn, joblib/joblib, zarr-developers/zarr-python, tornadoweb/tornado, sqlalchemy/sqlalchemy, getsentry/sentry-python, Textualize/textual, ipython/ipykernel, jupyter-server/jupyter_server, mitmproxy/mitmproxy, pydantic/pydantic-settings, pytest-dev/pytest-cov, pytest-dev/pytest-mock, pypa/cibuildwheel, pyvista/pyvista, geopandas/geopandas, encode/httpx, streamlink/streamlink, beetbox/beets. Six candidates were dropped before mining because GitHub reports `NOASSERTION` for their licence (hynek/structlog, pymc-devs/pymc, dateutil/dateutil, HypothesisWorks/hypothesis, celery/celery, python/typeshed), and pola-rs/polars because GitHub reports Rust. encode/httpx was registered in validation round 3, which kept no case from it; it gave no re-run case here either.

**Mining.** For each repository, the successful pull-request runs on a second or later attempt were listed (the runs listing returns at most 1,000 runs per query). They were examined in this order, newest first within each group: branches of the repository itself, then fork runs re-run by their own author, then the rest. A pass stopped at 40 runs examined or 5 cases mined per repository. altair, plotly.py, datasets and huggingface_hub had an earlier pass that stopped at 20 runs, and pip-tools a second pass after a fix to how commits are found. For each run, attempt 1's jobs were read; a first attempt with no failed job (awaiting approval or cancelled) was dropped. A first attempt where only pull-request metadata checks failed (template, title, labels, changelog entry) would have been dropped; none came up. The commits under test are the commits the pull request adds to the default branch; for a pull request since merged, they are taken from the base and head in the run's own checkout line.

**Drops before review:** 530 runs examined (not every listed run, since passes stopped early); 452 first attempts with no failed job, 8 with more than 15 commits under test, 4 whose log `gh` could not download, 3 merged pull requests with no checkout line to recover the commits from, 2 force-pushed. 61 cases were mined.

**Review.** Each mined case was read against its failed log (the error lines and the end of each failed job) and its commits. A case was kept only when the failure looked like something a re-run could fix (a network or download error, a timeout, a timing-sensitive or concurrent test, a runner or service fault) and the commits under test did not touch what failed. Of 61:

| verdict | cases |
|---|---|
| right | 39 |
| uncertain: only the gate job failed because jobs were cancelled, with no reason in the log | 5 |
| uncertain: the PR changed the code the failing test exercises | 9 |
| uncertain: the log does not show why the test failed | 7 |
| wrong: the PR added an API value the live service did not accept yet | 1 |

Of the 39 right, 1 is a duplicate (one loguru commit failing two workflows) and 1 is over the cap of 4 per repository (joblib; the lowest run ids are kept), so 37 are included. Each note is in `verdicts.csv`. A last check intersected the failing tests named in each included log with every file the commits under test changed, and found no overlap. Reading every file list in full (the first review saw only the first few files per commit) moved 2 cases to uncertain: ipykernel 33754574742 (the PR changed `kernelbase.py`) and sentry-python 44420766729 (the PR changed `tests/conftest.py`).

**Base branch.** A pull-request run tests a merge with the base branch. For a re-run of the same run this did not change: in three streamlink cases, attempt 2 checked out the same merge commit, with the same base, as attempt 1. Three is not a full check of all 37.

## What was excluded

Of 248 mined cases, 80 are left out. Every one is listed with its reason in `excluded.csv`.

| reason | cases |
|---|---|
| verdict uncertain (3 rule, 21 re-run) | 24 |
| verdict arguable | 10 |
| verdict-table entry cannot be matched (round 2 sphinx, see below) | 9 |
| verdict wrong, and the scope document does not state the right label (5 rule, 1 re-run) | 6 |
| duplicate (one commit failing several workflows; competing PRs for one change) | 14 |
| leak: a fix commit appears in what the agent reads | 1 |
| logs.jsonl over 40 MB | 1 |
| over the cap of 4 re-run cases per repository | 1 |
| default-branch runs (`scenarios-ci/main`) | 8 |
| fifth validation, never reviewed | 6 |

Included per round: first PR pass 10, round 1 17, round 2 38, round 3 28, round 4 38, re-run pass 37.

- **Wrong verdicts.** 19 rule labels were recorded as wrong. 14 have a right label stated in the scope document; 13 are included as `review` cases and 1 (scikit-learn 44711150450) is left out because a commit from its fix range appears in its log. 5 are left out because the document says why the rule misfired but not what the answer is: the three pip `ENOTFOUND` cases, pytest-xdist 40093733658 (a mixed failure), and scipy 44520410829 (crashed test workers, which the document says cannot be told from a segfault).
- **Round 2 sphinx.** The table lists "most of sphinx (8 of 12)" as right, but the round total of 43 needs 9, and the named counts in that row sum to 42. The table does not say which sphinx case is not right, so all 9 candidates are excluded. Two sphinx cases the table records as wrong, each from a commit that also failed one of those 9 workflows, are included as `review` cases.
- **Duplicates.** The first case by run id is kept. The starlette group is the six PRs whose titles fix the `URL.replace` port bug (#3684); a seventh PR that fixes it alongside other changes is kept as its own case. The three pytest-xdist cases are one runner change (Python 3.9 removed) hitting three PRs; the scope document counts them as three, and so does this benchmark. A duplicate of a case that is itself left out stands on its own.
- **Trace repair.** GitHub can rewrite a failed attempt's conclusion to success once a re-run passes, while the attempt's jobs keep theirs. The trace's root span then says the run passed. For every re-run case the freeze sets the root span to error when any job failed. This changed one case, pytest 42785732184, which was excluded for the leak before and is now included. The 37 new cases already had an error root span.
- **Size.** The 40 MB threshold was chosen after looking at the sizes. It excludes one case (dask 44522384834, 108 MB of log lines, 2.8 times the next largest). No log was truncated. The committed directory is 31 MB.

## Baselines

Blaming the head commit names the guilty commit in 102 of 108 `change` cases (94%) and fails all 60 `not_change` cases, where citing any commit fails:

| split | change | not_change | all |
|---|---|---|---|
| dev | 28 / 29 | 0 / 22 | 28 / 51 (55%) |
| test | 74 / 79 | 0 / 38 | 74 / 117 (63%) |
| both | 102 / 108 (94%) | 0 / 60 | 102 / 168 (61%) |

The number of commits under test separates the classes. 100 of 108 `change` cases have one commit; 24 of 60 `not_change` cases have more than one. Answering "not caused by a change" when there is more than one commit, and blaming the head commit otherwise, scores:

| split | change | not_change | all |
|---|---|---|---|
| dev | 27 / 29 | 9 / 22 | 36 / 51 (71%) |
| test | 73 / 79 | 15 / 38 | 88 / 117 (75%) |
| both | 100 / 108 (93%) | 24 / 60 (40%) | 124 / 168 (74%) |

A model has to beat this second baseline, not only the first. The scripted `rootcause eval --provider heuristic` baseline was not rerun on this version of the benchmark.

## Known limits

- **The two classes come partly from different repositories.** All 37 re-run cases come from 16 repositories that have no `change` case, and 108 of 108 `change` cases come from the first-pass repositories. A model could in principle learn which repositories have no `change` cases. The split by repository limits this: no repository in test is in dev, so dev cannot teach it which test repositories are which. It does not stop a model from using general signs of a repository's style.
- **Commit lists carry class signals.** Besides the commit count (see Baselines), 7 `not_change` cases list a commit that merges the base branch, and no `change` case does: the first-pass rules dropped cases whose fix range held a merge.
- **`not_change` is mostly one kind.** 37 of 60 `not_change` cases are re-run-passed failures: flaky tests, network and download errors, runner and service faults. Pre-release breakage, upstream releases and base-branch breaks are a handful of `review` cases.
- **Re-run passing is evidence, not proof.** A re-run can pass on a bug the PR added that fails only sometimes. The review excluded cases where the PR touched what failed, but it judged this from file names and failing tests, not by running anything.
- **Class balance.** 108 of 168 cases (64%) are `change`.
- **"Which commit" is nearly free.** See Baselines.
- **Not blind.** See Labels.
- **Not pre-registered.** The scope document says test truths are committed before any baseline runs. The scripted baseline ran on the earlier test set before it was committed.
- **Licences.** The scope required an OSI licence. At mining time GitHub reported `NOASSERTION` for 17 first-pass cases (django-rest-framework, networkx, numpy, cryptography, mypy, scikit-image, sympy, sphinx) and no licence for the 4 matplotlib cases. Each repository's licence file was then read by hand, and `labels.csv` records what it says: BSD-3-Clause for django-rest-framework, networkx, numpy, scikit-image and sympy, BSD-2-Clause for sphinx, MIT for mypy, and Apache-2.0 or BSD-3-Clause for cryptography. Matplotlib uses its own permissive licence, recorded as `LicenseRef-Matplotlib`; it is not on the OSI list, so the 4 matplotlib cases are an exception to the scope rule. The re-run repositories were checked before mining and all report an OSI licence.
- **Repositories are related.** Some share an organisation (pytest-dev, aio-libs, pallets, encode, dask, python-poetry, pydantic, ipython) and contributors; the split separates repositories, not organisations.
- **Coverage gates and lint failures** are filed as `bad_code_deploy`; the six categories have no place for them.
- **Small n.** 60 `not_change` cases, 22 in dev and 38 in test, give a 95% Wilson interval about 30 points wide on a per-class accuracy near 50% in test (35% to 65% at 19 of 38). Six pairs of re-run cases share one failure signature across two pull requests (the same timing test in tornado, the same coverage data-file crash in sentry-python, the same Windows file-replace permission error in cibuildwheel, the same dask test in joblib, the same truncated download in geopandas, the same PyPy error in poetry-core), so the cases are not all independent.
