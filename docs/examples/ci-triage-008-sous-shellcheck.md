# Example: `rootcause ci-triage` output

Produced offline from the recorded failed run in [`scenarios/008-sous-shellcheck/raw`](../../scenarios/008-sous-shellcheck/raw) (sous run 36960431348) with:

```bash
uv run rootcause ci-triage --raw scenarios/008-sous-shellcheck/raw --comment-md docs/examples/ci-triage-008-sous-shellcheck.md
```

This is the no-key heuristic provider, which blames the last commit, and its diagnosis here is wrong: the scenario's recorded truth ([`scenario.yaml`](../../scenarios/008-sous-shellcheck/scenario.yaml)) is `bad_code_deploy`, a new test script failing shellcheck, and the `.github/workflows/ci.yml` edit it cites is the planted decoy. `rootcause investigate scenarios/008-sous-shellcheck` grades this answer `fail`. The example shows the shape of the PR comment body; nothing is posted.

The comment body, exactly as written:

---

### rootcause: likely cause of this CI failure

**config_change** in `sous` (confidence 0.50)

Errors began after deploy 4870121d6d of sous.

Evidence: `4870121d6d`, `cfg:4870121d6d:.github/workflows/ci.yml`, `log-0009`

Suggested next step: Roll back 4870121d6d.

<sub>Investigated by heuristic. Review before acting.</sub>
