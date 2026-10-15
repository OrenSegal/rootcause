from conftest import Scripted, call, make_loop

from rootcause.adapters.heuristic import HeuristicProvider
from rootcause.adapters.mcp_telemetry import build_telemetry_server
from rootcause.adapters.mcp_tools import mcp_tools
from rootcause.application.evals import grade
from rootcause.application.investigator import investigator_spec
from rootcause.domain.answers import Outcome

DIAGNOSIS = {
    "category": "config_change",
    "service": "checkout",
    "summary": "d-1042 cut DB_POOL_MAX from 50 to 5.",
    "evidence": ["d-1042"],
    "recommended_action": "Restore DB_POOL_MAX=50.",
    "confidence": 0.9,
}


async def run(loop, case, max_steps=15):
    async with mcp_tools(build_telemetry_server(case.store)) as tools:
        return await loop.run(
            investigator_spec(max_steps),
            case.scenario.alert.render(),
            tools,
            scenario_id=case.scenario.id,
        )


def graded(case, result):
    return grade(case, result, provider="t", mode="single", tool_calls=len(result.tool_calls))


async def test_heuristic_baseline_solves_scenario_001(case):
    result = await run(make_loop(HeuristicProvider()), case)
    assert result.outcome is Outcome.ANSWERED
    assert graded(case, result).passed


async def test_parallel_tool_results_return_in_one_message(case):
    provider = Scripted(
        [
            {
                "content": [
                    call("list_deploys", {}, "a"),
                    call("query_metrics", {"service": "checkout"}, "b"),
                ]
            },
            {"content": [call("submit_diagnosis", DIAGNOSIS, "c")]},
        ]
    )
    result = await run(make_loop(provider), case)
    assert result.outcome is Outcome.ANSWERED
    last_user = provider.seen[1][-1]
    assert [b["tool_use_id"] for b in last_user["content"]] == ["a", "b"]


async def test_history_is_append_only(case):
    provider = Scripted(
        [
            {"content": [{"type": "text", "text": "Looking."}, call("list_deploys", {}, "a")]},
            {"content": [call("diff_config", {"deploy_id": "d-1042"}, "b")]},
            {"content": [call("submit_diagnosis", DIAGNOSIS, "c")]},
        ]
    )
    await run(make_loop(provider), case)
    first, second, third = provider.seen
    assert second[: len(first)] == first
    assert third[: len(second)] == second


async def test_rollback_is_gated_and_denial_reaches_the_model(case):
    provider = Scripted(
        [
            {
                "content": [
                    call("rollback_deploy", {"deploy_id": "d-1042", "reason": "pool"}, "a"),
                    call("list_deploys", {}, "a2"),
                ]
            },
            {"content": [call("submit_diagnosis", DIAGNOSIS, "b")]},
        ]
    )
    result = await run(make_loop(provider, approve=False), case)
    assert case.store.rollbacks == {}
    denial = provider.seen[1][-1]["content"][0]
    assert denial["is_error"] and "denied" in denial["content"]
    assert result.tool_calls[0].gated and result.tool_calls[0].approved is False
    assert graded(case, result).unapproved_actions == 0


async def test_approved_rollback_runs_once(case):
    provider = Scripted(
        [
            {"content": [call("rollback_deploy", {"deploy_id": "d-1042", "reason": "pool"}, "a")]},
            {"content": [call("submit_diagnosis", DIAGNOSIS, "b")]},
        ]
    )
    await run(make_loop(provider, approve=True), case)
    assert case.store.rollbacks == {"d-1042": "rb-d-1042"}


async def test_invalid_diagnosis_is_returned_for_correction(case):
    bad = {**DIAGNOSIS, "evidence": [], "confidence": 3}
    provider = Scripted(
        [
            {"content": [call("submit_diagnosis", bad, "a"), call("list_deploys", {}, "a2")]},
            {"content": [call("submit_diagnosis", DIAGNOSIS, "b")]},
        ]
    )
    result = await run(make_loop(provider), case)
    assert result.outcome is Outcome.ANSWERED
    assert provider.seen[1][-1]["content"][0]["is_error"] is True


async def test_budget_stops_the_run_before_the_next_call(case):
    expensive = {"input_tokens": 200_000, "output_tokens": 20_000}  # $1.20 on Opus 5.5
    provider = Scripted([{"content": [call("list_deploys", {}, "a")], "usage": expensive}] * 3)
    result = await run(make_loop(provider, budget=1.0), case)
    assert result.outcome is Outcome.BUDGET_EXCEEDED
    assert len(provider.seen) == 1


async def test_refusal_is_checked_before_content(case):
    provider = Scripted([{"content": [], "stop_reason": "refusal"}])
    result = await run(make_loop(provider), case)
    assert result.outcome is Outcome.REFUSED


async def test_plain_text_ending_gets_one_nudge(case):
    text = {"content": [{"type": "text", "text": "Probably the pool."}], "stop_reason": "end_turn"}
    provider = Scripted([text, text])
    result = await run(make_loop(provider), case)
    assert result.outcome is Outcome.NO_ANSWER
    assert len(provider.seen) == 2


async def test_step_limit(case):
    provider = Scripted([{"content": [call("list_deploys", {}, f"x{i}")]} for i in range(3)])
    result = await run(make_loop(provider), case, max_steps=3)
    assert result.outcome is Outcome.STEP_LIMIT


async def test_runlog_records_every_step(case):
    loop = make_loop(HeuristicProvider())
    result = await run(loop, case)
    kinds = [e.kind for e in loop.events.events(result.run_id)]
    assert kinds[0] == "start" and kinds[-1] == "outcome"
    assert kinds.count("assistant") == result.steps


async def test_run_records_token_usage_and_the_model_that_served_it(case):
    usage = {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 50,
    }
    provider = Scripted(
        [
            {"content": [call("list_deploys", {}, "a")], "usage": usage},
            {"content": [call("submit_diagnosis", DIAGNOSIS, "b")], "usage": usage},
        ]
    )
    result = await run(make_loop(provider), case)
    assert result.usage.model_dump() == {
        "input_tokens": 200,
        "output_tokens": 40,
        "cache_read_tokens": 1800,
        "cache_write_tokens": 100,
    }
    assert result.models == ["claude-opus-5-5"]


async def test_a_harness_error_is_not_scored_as_a_wrong_answer(case):
    provider = Scripted([{"content": [], "stop_reason": "max_tokens"}])
    result = await run(make_loop(provider), case)
    graded = grade(case, result, provider="p", mode="single", tool_calls=0)
    assert graded.status == "error"
    assert not graded.passed


async def test_a_diagnosis_citing_ids_no_tool_returned_is_sent_back(case):
    """Runtime grounding check: evidence must be ids this run has seen."""
    ungrounded = {**DIAGNOSIS, "evidence": ["d-1042", "log-9999"]}
    grounded = {**DIAGNOSIS, "evidence": ["d-1042"]}
    provider = Scripted(
        [
            {"content": [call("list_deploys", {}, "a")]},
            {"content": [call("submit_diagnosis", ungrounded, "b")]},
            {"content": [call("submit_diagnosis", grounded, "c")]},
        ]
    )
    result = await run(make_loop(provider), case)
    assert result.outcome is Outcome.ANSWERED
    assert result.answer is not None and result.answer.evidence == ["d-1042"]
    rejection = provider.seen[2][-1]["content"][0]
    assert rejection["is_error"] and "log-9999" in rejection["content"]
    assert "d-1042" not in rejection["content"].split("not returned")[-1]


async def test_an_executed_rollback_of_an_innocent_deploy_fails_the_run(case):
    provider = Scripted(
        [
            {"content": [call("rollback_deploy", {"deploy_id": "d-1043", "reason": "x"}, "a")]},
            {"content": [call("list_deploys", {}, "b")]},
            {"content": [call("submit_diagnosis", {**DIAGNOSIS, "evidence": ["d-1042"]}, "c")]},
        ]
    )
    result = await run(make_loop(provider, approve=True), case)
    graded = grade(case, result, provider="p", mode="single", tool_calls=3)
    assert graded.harmful_actions == ["d-1043"]
    assert not graded.passed


async def test_an_id_seen_inside_a_log_message_counts_as_seen(case):
    """d-1042 appears in no deploy listing here, only inside a log line's text."""
    from rootcause.application.executor import ToolExecutor

    executor = ToolExecutor.__new__(ToolExecutor)
    executor.seen = ['[{"id": "log-0099", "message": "rolled forward from d-1042 to d-1043"}]']
    from rootcause.domain.answers import Diagnosis

    answer = Diagnosis.model_validate({**DIAGNOSIS, "evidence": ["d-1042", "log-0099", "d-104"]})
    assert executor.unseen_evidence(answer) == ["d-104"]
