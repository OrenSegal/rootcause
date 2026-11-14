# rootcause

Architecture, layering rules and results are in `README.md`, `docs/decisions.md` and `docs/AUDIT.md`. The layer contracts in `pyproject.toml` and `tests/test_contracts.py` are enforced; do not loosen them to make a change fit.

## Checks

The same steps CI runs (`.github/workflows/ci.yml`):

```bash
uv sync --locked --all-extras   # ty fails on unresolved imports without the extras
uv run ruff format --check . && uv run ruff check . && uv run ty check && uv run lint-imports && uv run pytest -q
```

`rootcause eval --provider heuristic` and `rootcause retrieval-eval --retrieval bm25` need no API key. Any run with `--provider anthropic`, `claude-code` or `langgraph` spends money: ask first.

## Finishing Work

- Do not stop while you still owe work that the user asked for.
- Continue each part that does not depend on an open decision. Ask about the decision in the same message.
- Do not end a turn with an offer to continue. Do the next part.
- Diagnose an ordinary failure first: an error, a timeout, an empty result. Then fix the request, wait, or use a different tool.
- Retry only an action that is safe to repeat. Before you repeat a write, read the state to see if it completed. A sweep resumes on its own; a rollback must not run twice.
- Do not pass a deliberate block: a denied permission, a failing import contract, a `held_out` scenario list. Report it and find a different safe route.
- A risky or irreversible action still needs confirmation.
- "Done" means evidence from the current revision: the checks above, re-run, with their exit codes. For multi-part work, keep a ledger at `.scratch/<slug>/ledger.md` and have the `ledger-verifier` agent check it before you claim done.
- Do not add a code-ownership sentence ("this codebase is yours") to a brief. This repo wants surgical changes.
