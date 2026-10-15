excluded.jsonl holds runs removed from rows.jsonl by hand, with the reason:

- Retrieval configurations with zero tool calls: the telemetry server had not started (embedding model still loading past Claude Code's MCP startup wait), so the agent had no tools. Before fix #24 these were graded as wrong answers; the fixed harness reports them as errors. They were re-run.
