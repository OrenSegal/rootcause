"""The single-agent investigator: one agent, every telemetry tool, gated rollback."""

from rootcause.application.agent import AgentSpec
from rootcause.application.terminal import TerminalTool
from rootcause.domain.answers import Diagnosis

SUBMIT_DIAGNOSIS = TerminalTool("submit_diagnosis", Diagnosis)


def investigator_prompt(report: str, finish: str) -> str:
    """The investigator's instructions. Only how the answer is handed in differs
    between harnesses, so every row of a comparison gets the same brief."""
    return f"""\
You are an on-call investigator. An alert has fired. Find the root cause using the \
telemetry tools, then {report}.

How to work:
- Gather evidence before concluding. Logs, metrics, traces, deploys and config diffs \
are all available. Call independent tools in parallel.
- Symptoms spread: the paging service is not always the cause. Check what changed, \
and when, relative to the first errors.
- Cite evidence by the exact ids the tools returned (log ids, metric ids, span or \
trace ids, deploy ids, config change ids). Only cite ids you have seen.
- rollback_deploy changes production and needs operator approval. Only request it \
when the evidence supports it. If it is denied, finish the diagnosis anyway.
- If the evidence shows nothing is actually wrong, say so with category no_incident.

{finish}"""


INVESTIGATOR_PROMPT = investigator_prompt(
    report=f"report it by calling {SUBMIT_DIAGNOSIS.name} exactly once",
    finish=f"Finish by calling {SUBMIT_DIAGNOSIS.name}. Do not answer in plain text.",
)


def investigator_spec(max_steps: int = 15) -> AgentSpec[Diagnosis]:
    return AgentSpec(
        name="investigator",
        system_prompt=INVESTIGATOR_PROMPT,
        terminal=SUBMIT_DIAGNOSIS,
        allow_gated=True,
        max_steps=max_steps,
    )
