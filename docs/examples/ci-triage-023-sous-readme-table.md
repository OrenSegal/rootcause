# Example: `rootcause ci-triage` output, correct diagnosis

Produced offline from the recorded failed run in [`scenarios/023-sous-readme-table/raw`](../../scenarios/023-sous-readme-table/raw) (sous run 36972567801) with:

```bash
uv run rootcause ci-triage --raw scenarios/023-sous-readme-table/raw --comment-md docs/examples/ci-triage-023-sous-readme-table.md
```

This is the no-key heuristic provider, which blames the last commit. Here that is right: the scenario's recorded truth ([`scenario.yaml`](../../scenarios/023-sous-readme-table/scenario.yaml)) is `bad_code_deploy` in `sous` from commit 7dbc2abc9e, and `rootcause eval` grades this scenario `pass`. For a case where the same rule falls for a planted decoy, see [ci-triage-008-sous-shellcheck.md](ci-triage-008-sous-shellcheck.md). The example shows the shape of the PR comment body; nothing is posted.

The comment body, exactly as written:

---

### rootcause: likely cause of this CI failure

**bad_code_deploy** in `sous` (confidence 0.50)

Errors began after deploy 7dbc2abc9e of sous.

Evidence: `7dbc2abc9e`, `log-0020`

Suggested next step: Roll back 7dbc2abc9e.

<sub>Investigated by heuristic. Review before acting.</sub>
