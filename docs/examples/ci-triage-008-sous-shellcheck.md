# Example: `rootcause ci-triage` output, planted-decoy failure

Produced offline from the recorded failed run in [`scenarios/008-sous-shellcheck/raw`](../../scenarios/008-sous-shellcheck/raw) (sous run 36960431348) with:

```bash
uv run rootcause ci-triage --raw scenarios/008-sous-shellcheck/raw --comment-md docs/examples/ci-triage-008-sous-shellcheck.md
```

This is the no-key heuristic provider, which blames the last commit, and its diagnosis here is wrong: the scenario's recorded truth ([`scenario.yaml`](../../scenarios/008-sous-shellcheck/scenario.yaml)) is `bad_code_deploy`, a new test script failing shellcheck, and the `.github/workflows/ci.yml` edit it cites is the planted decoy. The heuristic files it as `config_change` on that edit, which the comment does not show: it names the right commit for the wrong reason, and its only log line is the exit code. `rootcause investigate scenarios/008-sous-shellcheck` grades this answer `fail`. With `--provider rules` no rule fires on this run, and the comment says so instead of guessing. For a recorded run it diagnoses correctly, see [ci-triage-023-sous-readme-table.md](ci-triage-023-sous-readme-table.md). The example shows the shape of the PR comment body; nothing is posted.

The comment body, exactly as written:

---

### CI failure triage

Job `test (ubuntu-latest)` failed at step `shellcheck`. The change under test likely caused it: commit `4870121d6d` (Add sous gate and sous fleet). Errors began after deploy 4870121d6d of sous.

The log lines behind it:

```text
shellcheck: Process completed with exit code 1.
```

<sub>rootcause 0.3.0, heuristic provider. Review before acting.</sub>
