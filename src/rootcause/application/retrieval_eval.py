"""Retrieval quality on its own, before it reaches an agent.

Each scenario's alert is the query, since that is all an investigator has when
it would first search. Relevance labels (`corpus/qrels.yaml`) name the past
postmortems that share the incident's mechanism. Scenarios with no relevant
postmortem are left out: an incident with no precedent is realistic, but it
has nothing to recall.
"""

import time
from collections.abc import Mapping, Sequence
from math import log2

from pydantic import BaseModel

from rootcause.application.ports import Retriever
from rootcause.application.stats import percentile


def recall_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    return len(set(ranked[:k]) & relevant) / len(relevant) if relevant else 0.0


def mrr(ranked: Sequence[str], relevant: set[str]) -> float:
    return next((1 / (i + 1) for i, d in enumerate(ranked) if d in relevant), 0.0)


def ndcg_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    dcg = sum(1 / log2(i + 2) for i, d in enumerate(ranked[:k]) if d in relevant)
    ideal = sum(1 / log2(i + 2) for i in range(min(k, len(relevant))))
    return dcg / ideal if ideal else 0.0


class RetrievalReport(BaseModel):
    retriever: str
    queries: int
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    mrr: float
    ndcg_at_5: float
    index_ms: float
    p50_query_ms: float
    p90_query_ms: float
    misses: list[str]  # scenarios with no relevant document in the top 5


def evaluate(
    retriever: Retriever,
    queries: Mapping[str, str],
    qrels: Mapping[str, Sequence[str]],
    *,
    index_ms: float,
) -> RetrievalReport:
    rows, latencies, misses = [], [], []
    for sid, relevant_list in sorted(qrels.items()):
        relevant = set(relevant_list)
        started = time.perf_counter()
        ranked = [h.doc_id for h in retriever.search(queries[sid], 10)]
        latencies.append((time.perf_counter() - started) * 1000)
        rows.append(
            (
                recall_at_k(ranked, relevant, 1),
                recall_at_k(ranked, relevant, 3),
                recall_at_k(ranked, relevant, 5),
                mrr(ranked, relevant),
                ndcg_at_k(ranked, relevant, 5),
            )
        )
        if not set(ranked[:5]) & relevant:
            misses.append(sid)
    n = len(rows) or 1
    mean = [sum(col) / n for col in zip(*rows, strict=True)] if rows else [0.0] * 5
    return RetrievalReport(
        retriever=retriever.name,
        queries=len(rows),
        recall_at_1=mean[0],
        recall_at_3=mean[1],
        recall_at_5=mean[2],
        mrr=mean[3],
        ndcg_at_5=mean[4],
        index_ms=index_ms,
        p50_query_ms=percentile(latencies, 50),
        p90_query_ms=percentile(latencies, 90),
        misses=misses,
    )
