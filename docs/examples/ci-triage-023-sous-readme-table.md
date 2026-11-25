# Example: `rootcause ci-triage` output, correct diagnosis

Produced offline from the recorded failed run in [`scenarios/023-sous-readme-table/raw`](../../scenarios/023-sous-readme-table/raw) (sous run 36972567801) with:

```bash
uv run rootcause ci-triage --raw scenarios/023-sous-readme-table/raw --comment-md docs/examples/ci-triage-023-sous-readme-table.md
```

This is the no-key heuristic provider, which blames the last commit. Here that is right: the scenario's recorded truth ([`scenario.yaml`](../../scenarios/023-sous-readme-table/scenario.yaml)) is `bad_code_deploy` in `sous` from commit 7dbc2abc9e, and `rootcause eval` grades this scenario `pass`. For a case where the same rule falls for a planted decoy, see [ci-triage-008-sous-shellcheck.md](ci-triage-008-sous-shellcheck.md). The example shows the shape of the PR comment body; nothing is posted.

The comment body, exactly as written:

---

### CI failure triage

Jobs `test (macos-latest)` and `test (ubuntu-latest)` failed. The change under test likely caused it: commit `7dbc2abc9e` (Add handoff mod: write LATEST.md and LATEST.patch after each turn). Errors began after deploy 7dbc2abc9e of sous.

The log lines behind it:

```text
install + doctor: Process completed with exit code 1.
```

<sub>rootcause 0.3.0, heuristic provider. Review before acting.</sub>
