"""The triage rules as an engine: free, deterministic, and allowed to abstain.

It reads the case's telemetry through the same store queries the tools serve (the
run's trace, the commits under test, each failed job's log lines) and never the
truth. Each read is logged as the tool call that returns the same data, without the
tools' line cap, so the grader sees a run that looked before it answered.

An abstention is a run with outcome `no_answer`: scored, never a pass, and distinct
from an answer in the sweep row. This engine gives `no_answer` for nothing else, and
its logged outcome carries the note `abstained: no rule fired`.
"""

import time
import uuid

from rootcause.application.agent import RunResult
from rootcause.application.executor import ToolCall
from rootcause.application.ports import EventLog
from rootcause.application.transcript import OUTCOME, START, TOOL_CALL
from rootcause.application.triage import failed_jobs, root_span, triage
from rootcause.domain.answers import Diagnosis, Outcome
from rootcause.domain.scenario import Case, Category
from rootcause.domain.telemetry import LogLine

AGENT = "rules"
ABSTAINED = "abstained: no rule fired"


class RulesEngine:
    name = AGENT

    async def investigate(self, case: Case, *, events: EventLog) -> RunResult[Diagnosis]:
        started = time.perf_counter()
        store = case.store
        run_id = events.start_run(scenario_id=case.scenario.id, agent=AGENT, provider=self.name)
        events.append(run_id, START, {"task": case.scenario.alert.render(), "tools": []})
        calls: list[ToolCall] = []

        def read(name: str, **args: object) -> None:
            calls.append(
                ToolCall(step=1, tool_use_id=f"rules_{uuid.uuid4().hex[:12]}", name=name,
                         args=dict(args), gated=False, attempts=1)
            )  # fmt: skip

        root = root_span(store.spans)
        spans = store.get_trace(root.trace_id) if root else []
        if root:
            read("get_trace", trace_id=root.trace_id)
        deploys = store.list_deploys()
        read("list_deploys")
        logs: list[LogLine] = []
        for job in failed_jobs(spans):
            logs += store.query_logs(service=job.service, limit=len(store.logs))
            read("query_logs", service=job.service, limit=len(store.logs))

        verdict = triage(spans, deploys, logs)
        answer, outcome, note = None, Outcome.NO_ANSWER, ABSTAINED
        if verdict is not None:
            change = verdict.truth_class == "change"
            answer = Diagnosis(
                category=Category.BAD_CODE_DEPLOY if change else Category.INFRASTRUCTURE,
                service=deploys[0].service if deploys else case.scenario.alert.service,
                summary=f"rule {verdict.rule}: {verdict.summary}",
                evidence=verdict.evidence,
                recommended_action="Fix the named commit." if change else "Re-run the job.",
                confidence=1.0,
            )
            outcome, note = Outcome.ANSWERED, ""
        result = RunResult[Diagnosis](
            run_id=run_id, agent=AGENT, outcome=outcome, answer=answer, steps=1,
            tool_calls=calls, cost_usd=0.0,
            wall_ms=round((time.perf_counter() - started) * 1000, 1), note=note,
        )  # fmt: skip
        for call in calls:
            events.append(run_id, TOOL_CALL, {"call": call.model_dump(mode="json"), "result": ""})
        events.append(run_id, OUTCOME, result.model_dump(mode="json"))
        return result
