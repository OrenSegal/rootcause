"""Single-source-of-truth checks: each contract is defined once, and these fail if
a second copy appears or two definitions drift apart."""

import ast
from pathlib import Path

from rootcause.application.investigator import SUBMIT_DIAGNOSIS
from rootcause.application.terminal import TerminalTool
from rootcause.domain.answers import Diagnosis, Findings, Outcome
from rootcause.domain.scenario import Alert, Category
from rootcause.interfaces.cli import EXIT_CODES

SRC = Path(__file__).resolve().parent.parent / "src" / "rootcause"


def _walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_diagnosis_tool_schema_is_generated_from_the_model():
    schema = SUBMIT_DIAGNOSIS.definition["input_schema"]
    assert schema["properties"]["category"]["enum"] == [c.value for c in Category]
    assert set(schema["required"]) == set(Diagnosis.model_fields)


def test_terminal_schemas_satisfy_strict_tool_use():
    for answer in (Diagnosis, Findings):
        tool = TerminalTool("t", answer)
        assert tool.definition["strict"] is True
        for node in _walk(tool.definition["input_schema"]):
            if isinstance(node, dict):
                assert "$ref" not in node and "minimum" not in node and "minItems" not in node
                if node.get("type") == "object":
                    assert node["additionalProperties"] is False
                    assert set(node["required"]) == set(node["properties"])


def test_bounds_dropped_from_the_schema_are_still_enforced():
    tool = TerminalTool("t", Diagnosis)
    bad = {
        "category": "config_change",
        "service": "x",
        "summary": "x",
        "evidence": [],
        "recommended_action": "x",
        "confidence": 3,
    }
    assert isinstance(tool.parse(bad), str)


def test_every_outcome_has_an_exit_code():
    assert set(EXIT_CODES) == set(Outcome)


def test_alert_text_round_trips():
    alert = Alert(service="checkout-api", summary="p99 above 2s: see dashboard")
    assert Alert.parse(alert.render()) == alert


def test_application_uses_sdk_data_types_but_never_the_client():
    """import-linter can't name subpackages of an external package, so this rule lives here."""
    offenders = []
    for path in (SRC / "application").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] == "anthropic" and not name.startswith("anthropic.types"):
                    offenders.append(f"{path.name}: {name}")
    assert offenders == []
