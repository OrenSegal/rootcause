"""The eval set itself is under test: consistent fixtures, no drift from the
authoring script, every category covered, and a baseline it can tell apart."""

import filecmp
import importlib.util
from pathlib import Path

import pytest
from conftest import SCENARIOS

from rootcause.adapters.fixtures import discover, load_case
from rootcause.adapters.heuristic import HeuristicProvider
from rootcause.adapters.sqlite_events import SqliteEventLog
from rootcause.composition import investigate
from rootcause.domain.scenario import Category

AUTHORING = SCENARIOS.parent / "scripts" / "author_scenarios.py"
CASES = discover(SCENARIOS)


def _authoring_module():
    spec = importlib.util.spec_from_file_location("author_scenarios", AUTHORING)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.name)
def test_truth_cites_evidence_that_exists(path: Path):
    assert load_case(path).unknown_truth_evidence() == []


def test_committed_fixtures_match_the_authoring_script(tmp_path: Path):
    authored = _authoring_module().SCENARIOS
    for builder in authored:
        builder.write(tmp_path)
        committed, fresh = SCENARIOS / builder.id, tmp_path / builder.id
        diff = filecmp.dircmp(committed, fresh)
        assert not (diff.diff_files or diff.left_only or diff.right_only), builder.id


def test_suite_covers_every_category():
    truths = [load_case(p).scenario.truth for p in CASES]
    seen = {t.category for t in truths if t is not None}
    assert seen == set(Category)


async def test_the_last_deploy_heuristic_does_not_solve_the_suite():
    """If the baseline passes everything, the evals can't show what a model adds."""
    passed = []
    for path in CASES:
        case = load_case(path)
        if case.scenario.provenance != "authored":
            continue
        run = await investigate(case, HeuristicProvider(), events=SqliteEventLog(), budget_usd=1.0)
        if run.grade.passed:
            passed.append(case.scenario.id)
    authored = [p for p in CASES if load_case(p).scenario.provenance == "authored"]
    assert len(authored) >= 20
    assert 0 < len(passed) <= len(authored) // 2, passed
