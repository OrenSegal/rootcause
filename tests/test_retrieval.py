"""Retrieval over past postmortems: metrics, the BM25 baseline, fusion, the corpus and
its relevance labels, and the tool's wiring into the telemetry server."""

from pathlib import Path

import pytest
from conftest import SCENARIOS

from rootcause.adapters.corpus import load_corpus, load_qrels
from rootcause.adapters.fixtures import discover, load_case
from rootcause.adapters.mcp_telemetry import build_telemetry_server
from rootcause.adapters.mcp_tools import mcp_tools
from rootcause.adapters.retrieval import BM25Retriever, fuse
from rootcause.application.retrieval_eval import mrr, ndcg_at_k, recall_at_k
from rootcause.application.sweep import Retrieval
from rootcause.domain.postmortems import Hit, Postmortem

CORPUS = SCENARIOS.parent / "corpus"


def doc(id_: str, title: str, body: str) -> Postmortem:
    return Postmortem(id=id_, title=title, date="2026-01-01", services=["x"], body=body)


def test_ranking_metrics_match_hand_computed_values():
    ranked = ["a", "b", "c", "d"]
    assert recall_at_k(ranked, {"b", "z"}, k=2) == 0.5
    assert recall_at_k(ranked, {"d"}, k=3) == 0.0
    assert mrr(ranked, {"c"}) == pytest.approx(1 / 3)
    assert mrr(ranked, {"z"}) == 0.0
    # one relevant doc at rank 2: DCG = 1/log2(3), ideal = 1
    assert ndcg_at_k(ranked, {"b"}, k=5) == pytest.approx(0.6309, abs=1e-4)


def test_bm25_prefers_the_document_that_shares_rare_terms():
    docs = [
        doc("p1", "Checkout latency", "checkout latency rose after a payments retry change"),
        doc("p2", "Pool exhaustion", "DB_POOL_MAX lowered in config, pool exhausted, timeouts"),
        doc("p3", "Search outage", "search pods restarted, latency rose"),
    ]
    hits = BM25Retriever(docs).search("timeout acquiring connection from pool DB_POOL_MAX", 2)
    assert hits[0].doc_id == "p2"
    assert all(isinstance(h, Hit) and h.score > 0 for h in hits)


def test_reciprocal_rank_fusion_rewards_agreement():
    a = [Hit(doc_id=d, title=d, score=1.0, snippet="") for d in ["x", "y", "z"]]
    b = [Hit(doc_id=d, title=d, score=1.0, snippet="") for d in ["y", "x", "w"]]
    fused = [h.doc_id for h in fuse([a, b], k=3)]
    assert set(fused[:2]) == {"x", "y"}
    assert "w" in fused or "z" in fused


def test_every_label_points_at_a_real_document_and_scenario():
    corpus = {d.id for d in load_corpus(CORPUS)}
    scenarios = {load_case(p).scenario.id for p in discover(SCENARIOS)}
    qrels = load_qrels(CORPUS)
    assert len(corpus) >= 25
    assert len(qrels) >= 15
    for sid, relevant in qrels.items():
        assert sid in scenarios, sid
        assert relevant and set(relevant) <= corpus, sid


async def test_search_tool_is_served_only_when_retrieval_is_on(case):
    async with mcp_tools(build_telemetry_server(case.store)) as tools:
        assert "search_postmortems" not in {t.name for t in tools}
    retriever = BM25Retriever(load_corpus(CORPUS))
    async with mcp_tools(build_telemetry_server(case.store, retriever=retriever)) as tools:
        search = next(t for t in tools if t.name == "search_postmortems")
        assert search.read_only
        result = await search.invoke({"query": "connection pool exhausted after config change"})
        assert not result.is_error and '"doc_id"' in result.text


def test_corpus_lives_outside_the_scenario_fixtures():
    """Postmortems are history the agent may read; nothing in them names a scenario's ids."""
    scenario_ids = set()
    for path in discover(SCENARIOS):
        scenario_ids |= load_case(path).store.evidence_ids()
    text = " ".join(d.body + d.title for d in load_corpus(CORPUS))
    leaked = sorted(i for i in scenario_ids if len(i) > 4 and i in text)
    assert leaked == [], leaked


def test_corpus_dir_exists():
    assert Path(CORPUS, "qrels.yaml").exists()


async def test_investigate_with_bm25_offers_the_search_tool(case):
    from conftest import Scripted, call

    from rootcause.adapters.sqlite_events import SqliteEventLog
    from rootcause.composition import investigate

    answer = {
        "category": "config_change", "service": "checkout", "summary": "s",
        "evidence": ["d-1042"], "recommended_action": "a", "confidence": 0.8,
    }  # fmt: skip
    provider = Scripted(
        [
            {"content": [call("search_postmortems", {"query": "pool exhausted"}, "a")]},
            {"content": [call("list_deploys", {}, "b")]},
            {"content": [call("submit_diagnosis", answer, "c")]},
        ]
    )
    run = await investigate(
        case, provider, events=SqliteEventLog(), budget_usd=1.0, retrieval=Retrieval.BM25
    )
    search = run.result.tool_calls[0]
    assert search.name == "search_postmortems" and not search.is_error
    assert run.grade.passed


async def test_claude_code_gets_the_retrieval_mode_in_its_server_config(case):
    import json

    from rootcause.adapters.claude_code import ClaudeCodeHarness
    from rootcause.adapters.sqlite_events import SqliteEventLog
    from rootcause.composition import investigate

    configs = []

    def read(path: str) -> dict:
        return json.loads(Path(path).read_text())

    commands = []

    async def spawn(cmd, cwd):
        configs.append(read(cmd[cmd.index("--mcp-config") + 1]))
        commands.append(cmd)
        return []

    await investigate(
        case, ClaudeCodeHarness(spawn=spawn), events=SqliteEventLog(), budget_usd=0.1,
        retrieval=Retrieval.BM25,
    )  # fmt: skip
    args = configs[0]["mcpServers"]["telemetry"]["args"]
    assert args[args.index("--retrieval") + 1] == "bm25"
    allowed = commands[0][commands[0].index("--allowedTools") + 1]
    assert "mcp__telemetry__search_postmortems" in allowed
    assert "search_postmortems" in commands[0][commands[0].index("--system-prompt") + 1]


def test_retrieval_eval_reports_every_labelled_scenario():
    from rootcause.composition import retrieval_eval

    report = retrieval_eval(Retrieval.BM25, SCENARIOS, CORPUS, query="alert")
    assert report.queries == len(load_qrels(CORPUS))
    assert 0.0 <= report.recall_at_5 <= 1.0
    assert report.retriever == "bm25"


def test_the_brief_mentions_postmortems_only_when_search_is_available():
    from rootcause.application.investigator import investigator_spec

    assert "search_postmortems" in investigator_spec(retrieval=True).system_prompt
    assert "search_postmortems" not in investigator_spec().system_prompt
