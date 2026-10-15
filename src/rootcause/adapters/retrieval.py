"""Retrievers over the postmortem corpus, from cheapest to most expensive.

- `BM25Retriever`: keyword scoring, written out here; no model, no download.
- `DenseRetriever` with a static embedder (model2vec): a lookup-table model of a
  few tens of MB, fast enough to embed on every query without a GPU.
- `DenseRetriever` with a transformer embedder (fastembed, ONNX): better at
  paraphrase, slower to index and to query.
- `HybridRetriever`: reciprocal rank fusion of BM25 and a dense ranking.

The embedders need the `rag` extra (`uv sync --extra rag`). Models are
downloaded from Hugging Face on first use and cached.
"""

import math
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any, Protocol

from rootcause.domain.postmortems import Hit, Postmortem

STOPWORDS = frozenset(
    "a an and are as at be by for from has in is it its of on or that the this to was "
    "were with after before into than then".split()
)
SNIPPET_CHARS = 700


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]


def _text(doc: Postmortem) -> str:
    return f"{doc.title}\n{' '.join(doc.services)}\n{doc.body}"


def _hit(doc: Postmortem, score: float) -> Hit:
    return Hit(
        doc_id=doc.id, title=doc.title, score=round(score, 4), snippet=doc.body[:SNIPPET_CHARS]
    )


class BM25Retriever:
    name = "bm25"

    def __init__(self, docs: Sequence[Postmortem], k1: float = 1.5, b: float = 0.75) -> None:
        self.docs = list(docs)
        self.k1, self.b = k1, b
        self.tf = [Counter(tokenize(_text(d))) for d in self.docs]
        self.lengths = [sum(tf.values()) for tf in self.tf]
        self.avg = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        df = Counter(term for tf in self.tf for term in tf)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int) -> list[Hit]:
        terms = tokenize(query)
        scored = []
        for doc, tf, length in zip(self.docs, self.tf, self.lengths, strict=True):
            score = 0.0
            for t in terms:
                if f := tf.get(t):
                    norm = f + self.k1 * (1 - self.b + self.b * length / self.avg)
                    score += self.idf[t] * f * (self.k1 + 1) / norm
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda p: -p[0])
        return [_hit(doc, s) for s, doc in scored[:k]]


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> Any: ...  # array of shape (len(texts), dim)


def _requires_rag(package: str) -> ImportError:
    return ImportError(f"{package} is not installed; install the rag extra: uv sync --extra rag")


class StaticEmbedder:
    """model2vec static embeddings: token vectors averaged, no attention at query time."""

    def __init__(self, model: str = "minishlab/potion-base-8M") -> None:
        try:
            from model2vec import StaticModel
        except ImportError as e:
            raise _requires_rag("model2vec") from e
        self.name = f"static:{model.rsplit('/', 1)[-1]}"
        self.model = StaticModel.from_pretrained(model)

    def embed(self, texts: list[str]) -> Any:
        return self.model.encode(texts)


class TransformerEmbedder:
    """A small sentence-transformer run through ONNX on the CPU."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5") -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as e:
            raise _requires_rag("fastembed") from e
        self.name = f"dense:{model.rsplit('/', 1)[-1]}"
        self.model = TextEmbedding(model)

    def embed(self, texts: list[str]) -> Any:
        import numpy as np

        return np.array(list(self.model.embed(texts)))


class DenseRetriever:
    def __init__(self, docs: Sequence[Postmortem], embedder: Embedder) -> None:
        import numpy as np

        self.np = np
        self.docs = list(docs)
        self.embedder = embedder
        self.name = embedder.name
        vectors = np.asarray(embedder.embed([_text(d) for d in self.docs]), dtype=float)
        self.vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    def search(self, query: str, k: int) -> list[Hit]:
        q = self.np.asarray(self.embedder.embed([query]), dtype=float)[0]
        scores = self.vectors @ (q / self.np.linalg.norm(q))
        order = self.np.argsort(-scores)[:k]
        return [_hit(self.docs[i], float(scores[i])) for i in order]


def fuse(rankings: Sequence[Sequence[Hit]], k: int, rrf_k: int = 60) -> list[Hit]:
    """Reciprocal rank fusion: a document scores 1/(rrf_k + rank) in each ranking."""
    scores: dict[str, float] = {}
    best: dict[str, Hit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            scores[hit.doc_id] = scores.get(hit.doc_id, 0.0) + 1 / (rrf_k + rank)
            best.setdefault(hit.doc_id, hit)
    order = sorted(scores, key=lambda d: -scores[d])[:k]
    return [best[d].model_copy(update={"score": round(scores[d], 4)}) for d in order]


class HybridRetriever:
    def __init__(self, keyword: BM25Retriever, dense: DenseRetriever, depth: int = 10) -> None:
        self.keyword, self.dense, self.depth = keyword, dense, depth
        self.name = f"hybrid:bm25+{dense.name}"

    def search(self, query: str, k: int) -> list[Hit]:
        return fuse(
            [self.keyword.search(query, self.depth), self.dense.search(query, self.depth)], k
        )
