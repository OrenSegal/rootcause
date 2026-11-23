# Contributing

Open an issue before a large change. Small fixes can go straight to a pull request.

## Checks

CI runs the same commands, so a green run locally should be green there.

```bash
uv sync --locked --all-extras
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run lint-imports
uv run pytest -q
uv run rootcause eval --min-pass-rate 0.05   # no API key, no spend
```

## Pull requests

One change per pull request. Say what changes and why. Add or update a test that fails without the change.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
