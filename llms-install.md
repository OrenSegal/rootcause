# Installing the rootcause MCP server

rootcause runs from a clone of this repository; it is not published to a package index. The server speaks MCP over stdio and exposes `list_scenarios`, `investigate_incident` and `run_evals`.

## Requirements

- git
- [uv](https://docs.astral.sh/uv/). It installs Python 3.14 and the locked dependencies itself.

## Steps

1. Clone the repository and install its dependencies:

   ```bash
   git clone https://github.com/OrenSegal/rootcause.git
   cd rootcause
   uv sync
   ```

2. Note the absolute path of the clone (`pwd`). Below it is `/ABSOLUTE/PATH/TO/rootcause`.

3. Add the server to the MCP client's settings:

   ```json
   {
     "mcpServers": {
       "rootcause": {
         "command": "uv",
         "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/rootcause", "rootcause", "mcp"]
       }
     }
   }
   ```

4. Check it: call `list_scenarios`. It returns the bundled incident scenarios.

## Providers

With no extra arguments the server uses the rule-based `heuristic` provider, which needs no API key and no network. To have a model investigate, append `"--provider", "anthropic"` to `args` and set the key in the server's environment:

```json
"env": { "ANTHROPIC_API_KEY": "<YOUR_ANTHROPIC_API_KEY>" }
```

`--budget-usd` (default `1.0`) caps spend per run. Run logs are written to `.rootcause/runs.sqlite` inside the clone.
