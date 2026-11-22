# Scope: a benchmark of real CI failures

Status: milestone 1 done; four validations on unseen repositories reached 74%, 89.6%, 69% and 85.7% (123 of 152 overall), short of the 90% target each time. Rule labels need a human review pass before they count as ground truth (see the end).

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

## Validation on unseen repositories (registered before mining, 2026-11-21)

The rules are frozen at this commit; nothing in `adapters/github_actions.py` changes until every kept case below has been checked by hand. Repositories, chosen for permissive licences and active pull requests, none seen by the rules: encode/starlette, pallets/click, python-attrs/attrs, psf/requests, tox-dev/tox, pypa/pip, Textualize/rich, aio-libs/aiohttp, scrapy/scrapy, marshmallow-code/marshmallow. Same window (since 2026-08-25), pull-request runs, at most 12 breaks each. The measure is the share of kept cases whose label (category and guilty commit) is right by hand.

### Validation result

**86 breaks, 27 kept. By hand, 20 of 27 labels are right (74%, Wilson 95% interval 55% to 87%), 2 are arguable and 5 are wrong.** Rules were not changed between registering the repositories and finishing this check.

| verdict | cases |
|---|---|
| right (20) | attrs 33951423062, 38477282538, 39835343871 and all 10 starlette cases (new code dropped coverage below 100%; the fix added tests or code); click 33887414469 (a test assertion), 34144555748 (mypy on the changed file); scrapy 42324865667 (the new test failed; the fix changed it); pip 42454330481 and scrapy 42512822031, 42716259868, 44441939033 (failed, then passed on a re-run; flaky) |
| arguable (2) | pip 42223885293, 44045555196: a "Check PR template" job that fails until the PR description is filled in, then passes on a re-run. No guilty commit is right; calling it infrastructure is a stretch |
| wrong (5) | click 33496785933 and pip 40913919882, 40929906813, 43157410626: the environment pattern matched `ENOTFOUND` inside `ModuleNotFoundError` and `FileNotFoundError`; the click case is a real bug (a test imported the POSIX-only `resource` module, and Windows failed). scrapy 43506370153: a mypy error that "passed on a re-run", because a pull-request re-run tests a fresh merge with the base branch, which had been fixed in between |

**Both failure sources are rule bugs, and both are fixable:**

1. `ENOTFOUND` was added to the environment pattern after the dev-set check, case-insensitively and without word boundaries. It should match `getaddrinfo ENOTFOUND` only.
2. For pull-request runs, "a re-run passed" does not mean flaky: the re-run tests a new merge commit. The rule needs the base branch to be unchanged between the two attempts, or should apply only to push runs.

**Other findings:**

- **Duplicates.** Six of the ten starlette cases are competing PRs fixing the same `URL.replace` bug. The labels are right, but a benchmark needs one per underlying change. Without starlette, 10 of 17 are right (59%, 36% to 78%).
- **Coverage gates are the most common real failure** (13 of 20 right cases), and the six categories have no place for them; they are filed as `bad_code_deploy`.
- **Yield** was 27 of 86 (31%). Approval-gated first attempts (23) and force-pushes (18) are the biggest drops.

**Verdict.** The pull-request approach works, and the remaining errors have specific causes, but 74% is not good enough for ground truth. After the two fixes, the next check needs another set of unseen repositories, with the target set before mining: at least 90% right on 30 or more kept cases, with duplicates collapsed.

## Second validation (registered before mining, 2026-11-21)

Rule fixes since the first validation: Node error codes (`ENOTFOUND` and the like) match only as whole, case-sensitive words; "a rerun passed" labels flaky only for push runs, and pull-request reruns are dropped. Rules are frozen at this commit until the hand check is done.

Repositories, none seen before: pallets/werkzeug, pallets/jinja, encode/uvicorn, agronholm/anyio, python-trio/trio, pypa/packaging, pypa/setuptools, aio-libs/yarl, sphinx-doc/sphinx, ipython/ipython, pydata/xarray, networkx/networkx. Pull-request runs since 2026-08-25, at most 15 breaks each.

Target, set now: **at least 90% of labels right by hand, on at least 30 kept cases after collapsing duplicates** (competing PRs for one underlying change count once, keeping the earliest). Below that, the rules are not ready to produce ground truth.

### Second validation result

**165 breaks, 48 kept, no competing-PR duplicates. By hand, 43 of 48 labels are right (89.6%, Wilson 95% interval 78% to 95%), 1 is arguable and 4 are wrong. That misses the registered target of 90%, narrowly.** Rules were not changed between registering and finishing the check.

| verdict | cases |
|---|---|
| right (43) | jinja (6), networkx (7), xarray (6), uvicorn (6), ipython (6), most of sphinx (8 of 12), anyio (2 of 3), yarl 44958570609. Lint and format failures on the files a commit touched, type-checker errors, coverage gates, failing tests, broken docs builds and benchmarks, each fixed in the files the guilty commit touched; workflow edits labelled config; two flaky linkcheck runs on one free-threaded job |
| arguable (1) | sphinx 42373832605: tests failed on pre-release Python 3.15, 3.16 and Docutils HEAD; the label says infrastructure because a test printed `Connection reset by peer`. Pre-release breakage is an environment change, but the rule fired for the wrong reason |
| wrong (4) | all four are environment failures labelled as code, because the author pushed more commits before CI went green: anyio 42467640327 (every test passed; the Coveralls upload step failed), sphinx 40744679692 (downloading Docutils from its git host failed), sphinx 44293155460 (pre-release Python setup failed, plus `SSLEOFError`), yarl 40324913764 (`DNSError` in the aiohttp integration job) |

The two bugs fixed after the first validation did not recur: `ModuleNotFoundError` no longer reads as a network error (ipython 42229657590 is labelled correctly as a Windows-only import in a test), and 11 pull-request reruns were dropped instead of being labelled flaky.

**What is left is one failure mode: environment failures the rules do not recognise**, labelled as code because the author kept pushing until CI went green. No single rule covers all four. Checked against this set after the fact:

- **A pre-release rule** (drop the case when every failed job runs a pre-release Python, an upstream HEAD or a `master` dependency) would catch sphinx 40744679692 and yarl 40324913764, plus the arguable sphinx 42373832605, and none of the 43 right cases.
- **A setup-step rule** (every failed step is a setup, install, download or upload step) would catch only sphinx 40744679692.
- anyio 42467640327 and sphinx 44293155460 fail in setup and test steps across stable and pre-release jobs at once. Nothing short of reading the log catches them.

With the pre-release rule, this set would score 43 of 45 (96%), but that figure is fitted on the set it is measured on. A third set of unseen repositories is the only honest way to claim it.

**Across both validations**, with the rules as they stood each time: 63 of 75 right (84%). The error causes changed between rounds, from rule bugs to one missing rule, which is the trend that matters.

## Third validation (registered before mining, 2026-11-21)

Rule added since the second validation: **a case is dropped when every failed job (gate jobs aside) tests an unreleased upstream**, judged by the job name (`HEAD`, `master`, `nightly`, `dev`, `pre-release`, `upstream`, `3.15-dev`, `3.16a1`). On every case mined so far it fires on exactly two, both wrong labels in the second validation (sphinx 40744679692, yarl 40324913764). Rules are frozen at this commit until the hand check is done.

Repositories, none seen before: encode/httpx, matplotlib/matplotlib, sympy/sympy, astropy/astropy, python-poetry/poetry, pypa/hatch, pypa/virtualenv, pre-commit/pre-commit, celery/celery, django/django, numpy/numpy, scipy/scipy, aio-libs/multidict, pytest-dev/pluggy. Pull-request runs since 2026-08-25, at most 15 breaks each.

Target, unchanged: **at least 90% of labels right by hand, on at least 30 kept cases after collapsing duplicates.**

**Top-up (registered 2026-11-21, after mining and before any hand check).** The 14 repositories gave 165 breaks but only 26 kept cases (first written here as 191 breaks, a summing error), under the 30-case floor (approval-gated first attempts and force-pushes took most of them). Labels were not inspected. Seven more unseen repositories, same window, cap and frozen rules: python/mypy, pandas-dev/pandas, scikit-learn/scikit-learn, aio-libs/frozenlist, aio-libs/propcache, pypa/build, mkdocs/mkdocs.

### Third validation result

**21 repositories, 237 breaks, 41 kept. Two duplicate groups (one matplotlib commit failing three workflows, one numpy commit failing five) collapse to 35 unique cases. By hand, 24 of 35 labels are right (69%, Wilson 95% interval 52% to 81%), 3 are arguable, 2 are uncertain and 6 are wrong. This misses the 90% target by a wide margin; counting every arguable and uncertain case as right still gives only 29 of 35 (83%).** Rules were not changed between registering and finishing the check.

| verdict | cases |
|---|---|
| right (24) | build 37202076420, 40533998006, 44219314063; django 44346380931; matplotlib 43908764069 (a workflow edit broke macOS jobs; config), 43926514982, 757845f1 (three workflows), 44933573494; multidict 37421469197, 37614237594, 37647784094, 37666235108, 37738480921, 37771426628, 38127829739; mypy 42514756929; numpy e1a3a616 (five workflows); pandas 44418822180, 44420380546, 44446651122, 44578641860; poetry 41204881585; propcache 36747921661 (metadata moved to `pyproject.toml` broke the build; config); scikit-learn 44788374900 |
| arguable (3) | mkdocs 44414107169: the commit needed formatting fixes, but the job failed when the auto-fix action could not upload them. multidict 38134575629: an inlining regression, but the fix range is 13 commits merged from the base branch. sympy 44931840961: a "Check Authors" job that passes once `.mailmap` lists the new author |
| uncertain (2) | pandas 44721698218: the saved log stops before the failing step. matplotlib 44944357599: stubtest output goes through reviewdog and is not in the log; the fix added the stub for the file the commit changed, which fits, but the error itself was not read |
| wrong (6) | **The base branch broke the PR:** scikit-learn 44703893474 (the base added a workflow with unpinned actions; the tested commit only edited an example; the fix range is one commit), poetry 39765933980 (Python 3.15 changed `CalledProcessError`'s message; a 26-commit merge of main fixed it). **Right commit, wrong category:** scikit-learn 44711150450 (the tested commit added `agentscan.yml` with an unpinned action, so `config_change`; labelled `bad_code_deploy` because the fix, a 25-commit merge of main, also changed code). **Environment errors the pattern does not know:** sympy 44524099951 (apt `Connection failed` while installing docs tools), scipy 44520410829 (pytest workers crashed on one Windows job). **Commit missed:** build 38002299563 (the commit switched CI to Python 3.15, which the runner did not have; labelled infrastructure with no commit because the environment rule fires first, but it is a config change by that commit) |

The pre-release rule fired once: scipy run 44518817161, where the only failed job was "Prerelease deps & coverage report". That drop is correct.

**What this round shows.** These repositories are bigger, and contributors merge the base branch into their PRs. Two new failure modes follow from that, and neither appeared in earlier rounds:

1. **The base branch changes under the PR.** A pull-request run tests a merge with the base, so a break on the base shows up as a PR failure (as with the scrapy case in the first validation). Sometimes the fix is a merge of main, and then "the files the fix changed" includes everything that landed there. Dropping cases whose fix range contains base-branch commits would catch poetry 39765933980 and scikit-learn 44711150450 (wrong category, which the drop would also remove) and the arguable multidict 38134575629. It would not catch scikit-learn 44703893474, whose fix is a single commit. That case needs the base compared between the red and green runs.
2. **A tested commit edited the workflow and the environment rule fired.** The environment rule should yield to a tested commit that changed the workflow file in the failing job.

Plus two more patterns for the environment rule (`Connection failed`, `Failed to fetch`, and pytest-xdist `worker ... crashed`).

**Across three validations**, with the rules as they stood each time: 87 of 110 right (79%). That pools figures under different duplicate conventions: round 3 collapses one commit failing several workflows, rounds 1 and 2 only collapsed competing PRs. Round 2 had two such commits (sphinx 40744679692/40744679694 and 44293155379/44293155460), each with one right and one wrong workflow. The second round's 89.6% did not hold on a new population. Each new set of repositories brings failure modes the earlier ones did not have, because how contributors work (merging the base, approval gates, pre-release jobs) varies by project.

**Verdict.** Commit-overlap labelling gets roughly 70% to 90% depending on how a project's contributors work. That is not good enough for unreviewed ground truth. The realistic path is labelling with rules plus a human review pass: the rules do the mining and propose a label,, and every kept case gets a check like the ones above against the failed log and both diffs. Adding more rules and running a fourth validation is possible, but each round so far has found new failure modes.

## Fourth validation (registered before mining, 2026-11-22)

Rules added since the third validation, each with a test:

- **The fix range contains a merge commit** (the author merged the base branch): drop, since the diff carries everything that landed on the base.
- **An environment error, but exactly one tested commit changed a workflow file the fix also changed:** `config_change` on that commit, not infrastructure.
- **`Failed to fetch http…` and apt's `Connection failed [IP…]`** join the environment pattern.
- **pytest-xdist reports a crashed worker:** drop (a segfault in the code and a starved runner look the same).

Re-labelling the 110 stored cases from the three validations with these rules changes only the cases already marked wrong (build 38002299563, sympy 44524099951, scipy 44520410829, plus the earlier fixes). The merge rule could not be replayed, because stored fix records lack parent counts. Its effect on earlier cases is unknown. Rules are frozen at this commit until the hand check is done.

Repositories, none seen before: python-pillow/Pillow, bokeh/bokeh, dask/dask, dask/distributed, pypa/twine, pypa/pipx, pyca/cryptography, urllib3/urllib3, nedbat/coveragepy, pytest-dev/pytest-asyncio, pytest-dev/pytest-xdist, python-jsonschema/jsonschema, holoviz/panel, encode/django-rest-framework, wagtail/wagtail, statsmodels/statsmodels, scikit-image/scikit-image. Pull-request runs since 2026-08-25, at most 15 breaks each.

Target, unchanged: **at least 90% of labels right by hand, on at least 30 unique cases.** Duplicates are counted once: one commit failing several workflows, and competing PRs for one change. If fewer than 30 unique cases come out, more unseen repositories are registered before any label is inspected, as in round 3.

### Fourth validation result

**17 repositories, 239 breaks, 45 kept. Three duplicate groups (two bokeh commits and one urllib3 commit, each failing two workflows) leave 42 unique cases. By hand, 36 of 42 labels are right (85.7%, Wilson 95% interval 72% to 93%), 2 are arguable and 4 are wrong. That misses the 90% target again, by less than round 3 did. Counting both arguable cases as right gives 38 of 42 (90.5%).** Rules were not changed between registering and finishing the check. Every verdict below was checked against the error in the failed log, not just file overlap.

| verdict | cases |
|---|---|
| right (36) | bokeh 9205686b (two workflows; a TypeScript error in the file the commit changed), 44361105539, 44680865048; coveragepy 43388470319 (pylint on the new stub), 43433858292, 43588543793; cryptography 43610216850 (coverage under 100%; the fix added a test vector); dask 43344036689, 43458367591, 44742477660 (mypy), 43545992148, 44522384834, 44864445442 (the commit's tests failed); distributed 36320289935; django-rest-framework 34093601968, 34686783870, 37662850565; panel 44339476406, 44383896934, 44397232490 (isort on a test file the commit touched); pipx 44001139987, 44661741143; pytest-asyncio 40571601184, 42167883825, 42619161278, 43909716468 (a new test used `asyncio.DefaultEventLoopPolicy`, gone in Python 3.16), 44882498211; pytest-xdist 40166502013, 42131392843, 43066656851 (the runner image dropped Python 3.9; infrastructure); scikit-image 44555313166; urllib3 44317305240, 44317333658, 20374a9e (two workflows), 44346684679, 44754535247 |
| arguable (2) | bokeh 7ea407f3 (two workflows): the PR's `pan_tool.ts` used an API the base branch had just removed, and a merge brought the two together; the label blames the PR commit, which the fix edited. dask 44533673971: a strict xfail the commit kept started passing on upstream nightlies; the commit is right, but the trigger was upstream |
| wrong (4) | **Flaky browser tests on one OS:** panel 43759376300 and 43954653640 (docs-only commits; Playwright UI tests failed on Windows or macOS), panel 44356789031 (unrelated Tabulator and notebook UI tests failed on macOS). The matrix rule should have dropped them, but the job names (`ui:test-ui:windows-latest`) have no space, so the "first word" was the whole name and no siblings matched. **Mixed failure:** pytest-xdist 40093733658: the commit's new test failed on Windows, while Python 3.9 jobs failed setup on a runner without 3.9; the environment rule fires on one match anywhere in the log, so it labelled infrastructure |

**The new rules.** The merge rule dropped 10 cases and the worker-crash rule 4; whether those drops were right was not checked. The workflow-edit rule did not fire. Three pytest-xdist PRs hit the same runner change (Python 3.9 removed); they count as three cases, not one, because they are not competing fixes for one change.

**Both wrong-label causes are rule bugs with a clear fix:**

1. Matrix siblings should be grouped by the job name up to the first space, colon, slash or parenthesis, not up to the first space.
2. The environment rule should require an environment error in every failed job's log, not just somewhere in the combined log.

**Across four validations**, with the rules as they stood each time: 123 of 152 right (81%). Per round: 74%, 89.6%, 69%, 85.7%.

**Verdict.** The target has not been met in four rounds, but the last round came within one case of it if the arguable ones count, and its errors are two specific rule bugs rather than a new failure mode. The rules-plus-review recommendation stands: with a review pass, these 42 cases yield 36 confirmed labels after about an hour of checking. A fifth round would test the two fixes, but at this point the honest claim is "about 85% on unseen repositories, reviewed by hand", not "90% automatic".
