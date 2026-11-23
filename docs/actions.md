# Actions

This repository ships three GitHub Actions. The root action, `OrenSegal/rootcause`, is the eval gate described in the README and is unchanged. This page covers the other two:

- `OrenSegal/rootcause/triage` says whether a failed CI run was caused by the change under test.
- `OrenSegal/rootcause/diff` compares two eval result files and fails the job on a real regression.

Both install the tool from the action's own ref, not from your repository. The examples use `@v0.3.0`; you can pin the full commit sha of that tag instead.

## Triage a failed CI run

When your CI workflow fails, this action reads the failed run's jobs, log and commits, and writes a short verdict to the job summary: what failed, whether the change under test likely caused it and which commit, and the log lines behind it. The default `rules` provider is free, needs no API key and gives the same answer every time. When no rule fires, the verdict says "No rule fired; this needs a person or a model." and does not guess.

Add this file as `.github/workflows/triage.yml`. Replace `ci` with the name of your CI workflow.

```yaml
name: triage
on:
  workflow_run:
    workflows: [ci]
    types: [completed]
permissions:
  contents: read
  actions: read
jobs:
  triage:
    if: github.event.workflow_run.conclusion == 'failure'
    runs-on: ubuntu-latest
    steps:
      - uses: OrenSegal/rootcause/triage@v0.3.0
```

A verdict looks like this:

> Job `pre-commit` failed at step `Error out on pre-commit errors`. The change under test likely caused it: commit `8c59d8c7c5` (Generate GH actions worklow lockfile). The failing log lines name files only commit 8c59d8c7c5 changed.

It is followed by the log lines it cites and a footer naming the provider and the rule that fired.

### Why `workflow_run`

The triage runs in a separate workflow, triggered when CI completes. That workflow runs in your repository's context with your token, so it can read the logs of a pull request from a fork. The action never checks out or runs the pull request's code. It only reads the run and its commits through `gh`.

### Inputs

| input | default | meaning |
|---|---|---|
| `provider` | `rules` | `rules` (no key, abstains when no rule fires) or `anthropic` |
| `anthropic-api-key` | | required for `anthropic`; pass it from a secret |
| `model` | `claude-opus-5-5` | model id for `anthropic` |
| `budget-usd` | `0.50` | spending cap for one triage with `anthropic` |
| `comment` | `false` | `true` also posts the verdict as a pull-request comment |
| `pr-number` | the run's pull request | the pull request to comment on |
| `run-id` | the run that triggered the workflow | the failed run to triage |
| `repository` | this repository | `owner/name` of the run |
| `github-token` | `github.token` | token for the `gh` calls |

The output `verdict` is the path to the verdict markdown.

### Permissions

The default needs only `contents: read` and `actions: read`, and writes only the job summary. In a private repository, also grant `pull-requests: read`, so the action can list the pull request's commits.

To post a comment, set `comment: "true"` and grant `pull-requests: write`:

```yaml
permissions:
  contents: read
  actions: read
  pull-requests: write
```

GitHub does not fill in the pull request of a run from a fork. In that case no comment is posted and the summary says so. Each failed run posts a new comment.

### Using a model for what the rules leave

```yaml
      - uses: OrenSegal/rootcause/triage@v0.3.0
        with:
          provider: anthropic
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

The rules and a model are separate runs: a model triages every failure, not only the ones the rules abstain on.

## Gate on an eval regression

This action compares a baseline and a candidate result file case by case. It writes the comparison to the job summary and fails the job when the baseline is better than the candidate by more than noise: the paired 95% interval lies below zero and a sign test gives p < 0.05. It works in any repository; nothing about it is specific to rootcause.

Each file is JSONL with one run per line. `cost` is optional, and a case may have several runs:

```json
{"case": "refund-policy", "passed": true, "cost": 0.012}
{"case": "address-change", "passed": false}
```

```yaml
on: pull_request
jobs:
  diff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
      - uses: OrenSegal/rootcause/diff@v0.3.0
        with:
          baseline: evals/main.jsonl
          candidate: evals/pr.jsonl
```

### Inputs

| input | default | meaning |
|---|---|---|
| `baseline` | required | JSONL results of the baseline, relative to the workspace |
| `candidate` | required | JSONL results of the candidate |
| `intersect` | `false` | `true` drops cases present on one side only |
| `cases` | | a file of case ids, one per line, to compare only those |
| `fail-on-regression` | `true` | `false` reports without failing the job |

The output `diff` is the path to the comparison markdown.

### Permissions

`contents: read` is enough; add `permissions: {contents: read}` to the workflow to say so. The action reads the two files and writes the job summary.
