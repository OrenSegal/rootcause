import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "microagent", Path(__file__).parent.parent / "micro" / "microagent.py"
)
assert _spec and _spec.loader
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_scripted_baseline_falls_for_every_decoy():
    by_incident, rate, (lo, hi) = m.evaluate(m.scripted, runs=1)
    assert [i for i, p in by_incident.items() if all(p)] == ["bad-deploy"]
    assert rate == 0.2 and lo <= rate <= hi


def _rollback_then_answer(messages):
    if not any(x["role"] == "assistant" for x in messages):
        return [dict(type="tool_use", id="a", name="rollback", input={"deploy_id": "d1"})]
    answer = dict(service="payments", category="dependency", evidence=["l1"])
    return [dict(type="tool_use", id="b", name="diagnose", input=answer)]


def test_gate_and_grader_catch_an_innocent_rollback():
    denied = dict(m.INCIDENTS[0], rolled_back=[])
    assert m.grade(denied, m.investigate(denied, _rollback_then_answer, approve=lambda a: False))
    approved = dict(m.INCIDENTS[0], rolled_back=[])
    answer = m.investigate(approved, _rollback_then_answer, approve=lambda a: True)
    assert not m.grade(approved, answer)
