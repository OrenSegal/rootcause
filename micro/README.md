# microagent

rootcause in one file of stdlib Python, 247 lines: a tool loop, an approval gate on the one tool that changes anything, five incidents with known causes (four with decoys), a grader, and a pass rate with a 95% interval from resampling incidents.

```bash
python micro/microagent.py                                          # scripted baseline: no key, no spend
ANTHROPIC_API_KEY=... python micro/microagent.py --model claude-haiku-5-5 --runs 3
```

The scripted baseline blames the last deploy before the first error and passes 1 of 5. Read it top to bottom; the numbered comments are the six parts of every agent eval. The full version, with MCP, crash recovery, OpenTelemetry, retrieval and 33 incidents, is the rest of this repo.
