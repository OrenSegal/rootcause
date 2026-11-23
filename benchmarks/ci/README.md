# CI failure benchmark

117 failed GitHub Actions runs on pull requests from 37 public Python repositories. For each one the question is: did the change under test cause this failure, and if so which commit, with evidence? Or did something else cause it (a flaky test, the runner image, the network, an upstream release)?

## What a case is

A case is one failed workflow run, recorded as a scenario directory that `rootcause eval` loads:

- `scenario.yaml`: the alert (workflow, branch, head commit, failed job and step), the pull-request title, and the truth.
- `logs.jsonl.gz`: the failed jobs' log lines (gzipped; the loader reads it directly).
- `traces.json`: the run, its jobs and steps as spans.
- `deploys.json`: the commits under test, with the files each one changed.
- `metrics.json`: empty.

The truth is either `bad_code_deploy` or `config_change` citing the guilty commit (class `change`), or `infrastructure` citing failed log lines and no commit (class `not_change`). The service is always the repository name.

`provenance/<case>/` keeps the mined `scenario.yaml` with its comment header (run URL, fix run, label rule), and `run.json`, `commits.json` and `fix.json` from the mining. No telemetry tool reads this directory. The raw failed log is not kept; the fixtures were built from it.

```bash
uv run rootcause eval benchmarks/ci/dev --provider heuristic --min-pass-rate 0
```

## Where it came from

`scripts/mine_ci.py` found red-to-green pairs on pull-request branches, runs since 2026-08-25, in the repositories registered for the first PR pass and validation rounds 1 to 4 of `docs/ci-benchmark-scope.md`. Automatic rules proposed a label for each kept break. Each proposed label was then reviewed against the failed log and both diffs, and the scope document records every verdict as right, arguable, uncertain or wrong.

`verdicts.csv` copies those tables by hand, one row per mined case. `scripts/freeze_ci_benchmark.py` reads it and the mined cases (kept locally in `scenarios-ci/`, not committed) and writes this directory. Running it twice gives identical files.

## Labels

Only cases a verdict table records as right are included. No label was changed: the script checks that each stored label agrees with what the table says (class, and the category or commit where the table names one), and none disagreed. The same author's rules proposed every label, and the review judged the proposed label, so it was not blind. The 30-case blind audit with Cohen's kappa planned in the scope document was not run, and no agreement figure exists.

## Counts

| | change | not_change | cases | repositories |
|---|---|---|---|---|
| dev | 27 | 2 | 29 | 10 |
| test | 79 | 9 | 88 | 27 |
| all | 106 | 11 | 117 | 37 |

Categories: 100 `bad_code_deploy`, 6 `config_change`, 11 `infrastructure`.

Dev is for tuning an agent; test is held out. The split is by repository, so no repository is in both. Repositories were shuffled with seed 20261123 and added to dev until it held about a quarter of the cases; the first shuffle with both classes in both splits, and at most one case over a quarter of each class in dev, was used.

| repository | split | change | not_change |
|---|---|---|---|
| bokeh/bokeh | dev | 3 | |
| dask/dask | dev | 5 | |
| nedbat/coveragepy | dev | 3 | |
| pallets/click | dev | 2 | |
| pallets/jinja | dev | 6 | |
| pydata/xarray | dev | 6 | |
| pypa/pip | dev | | 1 |
| pytest-dev/pytest | dev | | 1 |
| python-poetry/poetry | dev | 1 | |
| scikit-learn/scikit-learn | dev | 1 | |
| agronholm/anyio | test | 2 | |
| aio-libs/multidict | test | 7 | |
| aio-libs/propcache | test | 1 | |
| aio-libs/yarl | test | 1 | |
| dask/distributed | test | 1 | |
| django/django | test | 1 | |
| encode/django-rest-framework | test | 3 | |
| encode/starlette | test | 5 | |
| encode/uvicorn | test | 6 | |
| fastapi/fastapi | test | 4 | |
| holoviz/panel | test | 3 | |
| ipython/ipython | test | 6 | |
| matplotlib/matplotlib | test | 4 | |
| networkx/networkx | test | 7 | |
| numpy/numpy | test | 1 | |
| pandas-dev/pandas | test | 4 | |
| pyca/cryptography | test | 1 | |
| pydantic/pydantic | test | 1 | 3 |
| pypa/build | test | 3 | |
| pypa/pipx | test | 2 | |
| pytest-dev/pytest-asyncio | test | 5 | |
| pytest-dev/pytest-xdist | test | | 3 |
| python-attrs/attrs | test | 3 | |
| python/mypy | test | 1 | |
| scikit-image/scikit-image | test | 1 | |
| scrapy/scrapy | test | 1 | 3 |
| urllib3/urllib3 | test | 5 | |

## What was excluded

Of 187 mined cases, 70 are left out. Every one is listed with its reason in `excluded.csv`.

| reason | cases |
|---|---|
| verdict wrong | 19 |
| verdict arguable | 10 |
| verdict uncertain | 3 |
| verdict-table entry cannot be matched (round 2 sphinx, see below) | 9 |
| duplicate (one commit failing several workflows; competing PRs for one change) | 13 |
| leak: the trace says the run passed | 1 |
| logs.jsonl over 40 MB | 1 |
| default-branch runs (`scenarios-ci/main`) | 8 |
| fifth validation, never reviewed | 6 |

Included per round: first PR pass 9, round 1 15, round 2 34, round 3 24, round 4 35.

- **Round 2 sphinx.** The table lists "most of sphinx (8 of 12)" as right, but the round total of 43 needs 9, and the named counts in that row sum to 42. The table does not say which sphinx case is not right, so all 9 candidates are excluded.
- **Duplicates.** The first case by run id is kept. The starlette group is the six PRs whose titles fix the `URL.replace` port bug (#3684); a seventh PR that fixes it alongside other changes is kept as its own case. The three pytest-xdist cases are one runner change (Python 3.9 removed) hitting three PRs; the scope document counts them as three, and so does this benchmark.
- **Leak.** In pytest 42785732184 the first attempt's conclusion had been rewritten to success after the rerun passed, so the trace's root span said the run succeeded.
- **Size.** The 40 MB threshold was chosen after looking at the sizes. It excludes one case (dask 44522384834, 108 MB of log lines, 2.8 times the next largest). No log was truncated.

## Known limits

- **Class balance is skewed.** 106 of 117 cases (91%) are `change`. Dev has only 2 `not_change` cases, test 9. The mining rules drop many likely flakes before review (pull-request reruns from round 2 on, crashed test workers, lone failing matrix jobs), and default-branch runs, mostly flakes in milestone 1, are left out. Excluding wrong verdicts also removes more of this class than the other: by the tables' descriptions, 11 of the 19 wrong verdicts were failures the change did not cause but the rules blamed on a commit, and 7 went the other way.
- **"Which commit" is nearly free.** 104 of 117 cases (89%) have one commit under test. Always blaming the head commit names the guilty commit in 100 of 106 `change` cases (94%) and is wrong on all 11 `not_change` cases (0%), where citing any commit fails.
- **The scripted baseline.** `rootcause eval --provider heuristic` ("blame the last deploy before the first error") passes 24 of 29 dev cases and 74 of 88 test cases. Of its 8 misses on `change` cases, 5 cite the newest commit when an earlier one is guilty, 2 cite the right commit under the wrong category, and 1 (propcache 36747921661) gets both wrong:

  | split | change | not_change | all |
  |---|---|---|---|
  | dev | 24 / 27 | 0 / 2 | 24 / 29 (83%) |
  | test | 74 / 79 | 0 / 9 | 74 / 88 (84%) |
  | both | 98 / 106 (92%) | 0 / 11 | 98 / 117 (84%) |

  A model has to beat this on `change` cases and separate the `not_change` ones.
- **Not blind.** The review saw the proposed label (see Labels).
- **Not pre-registered.** The scope document says test truths are committed before any baseline runs. The scripted baseline above ran on test before this directory was committed.
- **Licences.** The scope required an OSI licence. At mining time GitHub reported `NOASSERTION` for 14 cases (django-rest-framework, networkx, numpy, cryptography, mypy, scikit-image) and no licence for the 4 matplotlib cases. The licence was not checked by hand.
- **Repositories are related.** Some share an organisation (pytest-dev, aio-libs, pallets, encode, dask) and contributors; the split separates repositories, not organisations.
- **Coverage gates and lint failures** are filed as `bad_code_deploy`; the six categories have no place for them.
- **Small n for `not_change`.** 11 cases from 5 repositories cannot support a per-class accuracy claim with a useful interval.
