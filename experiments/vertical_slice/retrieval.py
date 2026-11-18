"""BM25, dense, and evidence-closed graph retrieval for the isolated corpus."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from typing import Any

from experiments.vertical_slice.models import Corpus, derived_id
from experiments.vertical_slice.providers import FakeEmbeddingProvider


STOPWORDS = {"a", "an", "and", "are", "does", "how", "is", "of", "or", "the", "to", "what", "which"}


def tokenize(text: str) -> list[str]:
    expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return [token for token in re.findall(r"[a-z0-9_]+", expanded.casefold()) if token not in STOPWORDS]


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    score: float
    channel: str
    rank: int


class BM25Index:
    """Small in-memory BM25 implementation; index export is safe JSON."""

    def __init__(self, chunks: list[Any], k1: float = 1.2, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.tokens = [tokenize(chunk.text) for chunk in chunks]
        self.lengths = [len(items) for items in self.tokens]
        self.average_length = sum(self.lengths) / max(1, len(self.lengths))
        self.document_frequency: Counter[str] = Counter()
        self.term_frequency: list[Counter[str]] = []
        for items in self.tokens:
            tf = Counter(items)
            self.term_frequency.append(tf)
            self.document_frequency.update(tf.keys())

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        query_terms = tokenize(query)
        n = len(self.chunks)
        scores: list[tuple[float, str]] = []
        for chunk, tf, length in zip(self.chunks, self.term_frequency, self.lengths, strict=True):
            score = 0.0
            for term in query_terms:
                frequency = tf.get(term, 0)
                if not frequency:
                    continue
                df = self.document_frequency[term]
                inverse = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
                denominator = frequency + self.k1 * (1.0 - self.b + self.b * length / max(1.0, self.average_length))
                score += inverse * frequency * (self.k1 + 1.0) / denominator
            if score > 0:
                scores.append((score, chunk.id))
        scores.sort(key=lambda item: (-item[0], item[1]))
        return [SearchHit(chunk_id, score, "bm25", rank) for rank, (score, chunk_id) in enumerate(scores[:top_k], 1)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tokenizer": "lowercase-camelcase-alnum-stopword-v1",
            "k1": self.k1,
            "b": self.b,
            "document_count": len(self.chunks),
            "documents": [
                {"chunk_id": chunk.id, "tokens": tokens, "length": length}
                for chunk, tokens, length in zip(self.chunks, self.tokens, self.lengths, strict=True)
            ],
        }


class InMemoryDenseIndex:
    """Deterministic fake vector search used only for offline engineering paths."""

    def __init__(self, chunks: list[Any], generation_id: str, embedder: Any | None = None) -> None:
        self.chunks = chunks
        self.generation_id = generation_id
        self.embedder = embedder or FakeEmbeddingProvider()
        self.fingerprint = f"{self.embedder.model}:{self.embedder.dimension}:camelcase-stopword-sha256-sign-v2"
        self.vectors = {chunk.id: self.embedder._vector(chunk.text) for chunk in chunks}

    async def search(self, query: str, top_k: int = 5, generation_id: str | None = None) -> list[SearchHit]:
        if generation_id and generation_id != self.generation_id:
            raise ValueError("dense search generation mismatch")
        if f"{self.embedder.model}:{self.embedder.dimension}:camelcase-stopword-sha256-sign-v2" != self.fingerprint:
            raise ValueError("dense query/index fingerprint mismatch")
        query_vector = self.embedder._vector(query)
        scores = [
            (sum(a * b for a, b in zip(query_vector, self.vectors[chunk.id], strict=True)), chunk.id)
            for chunk in self.chunks
        ]
        scores.sort(key=lambda item: (-item[0], item[1]))
        return [SearchHit(chunk_id, score, "dense_fake", rank) for rank, (score, chunk_id) in enumerate(scores[:top_k], 1) if score > 0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "embedding_fingerprint": self.fingerprint,
            "provider": "fake-hash-embedding-v1",
            "vectors": self.vectors,
        }


class QdrantCandidateIndex:
    """CandidateIndex wrapper around the P3 Qdrant REST adapter."""

    def __init__(self, dense_index: Any) -> None:
        self.dense_index = dense_index

    async def build(self, chunks: list[Any], generation_id: str, corpus_id: str) -> int:
        return await self.dense_index.build(chunks, generation_id, corpus_id)

    async def search(self, query: str, top_k: int = 5, generation_id: str | None = None) -> list[SearchHit]:
        if generation_id and self.dense_index.generation_id != generation_id:
            raise ValueError("Qdrant candidate index generation mismatch")
        rows = await self.dense_index.search(query, top_k)
        return [
            SearchHit(item["chunk_id"], float(item["score"]), "dense", rank)
            for rank, item in enumerate(rows, 1)
            if item.get("chunk_id")
        ]


@dataclass(frozen=True)
class GraphPath:
    id: str
    entity_names: tuple[str, ...]
    relation_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    source_kinds: tuple[str, ...]


class GraphRetriever:
    def __init__(self, corpus: Corpus, allowed_source_ids: set[str] | None = None) -> None:
        self.corpus = corpus
        self.allowed_source_ids = allowed_source_ids
        self.evidence_by_id = {item.id: item for item in corpus.evidence}
        self.relations = [
            relation
            for relation in corpus.relations
            if all(
                evidence_id in self.evidence_by_id
                and (allowed_source_ids is None or self.evidence_by_id[evidence_id].source_id in allowed_source_ids)
                for evidence_id in relation.evidence_ids
            )
        ]
        self.entities_by_id = {item.id: item.canonical_name for item in corpus.entities.values()}

    def two_hop(self, query: str) -> list[GraphPath]:
        if "pipeline" not in query.casefold() and "ingestion" not in query.casefold():
            return []
        target_requested = "span" in query.casefold()
        starts = [item for item in self.corpus.entities.values() if item.canonical_name == "IngestionPipeline"]
        targets = {item.id for item in self.corpus.entities.values() if item.canonical_name == "SourceSpan"} if target_requested else set()
        outgoing: dict[str, list[Any]] = defaultdict(list)
        for relation in self.relations:
            outgoing[relation.subject_id].append(relation)
        results: list[GraphPath] = []
        for start in starts:
            queue = deque([(start.id, [start.canonical_name], [], [])])
            while queue:
                node_id, names, relation_ids, evidence_ids = queue.popleft()
                if len(relation_ids) >= 2:
                    continue
                for relation in outgoing.get(node_id, []):
                    next_names = names + [relation.object]
                    next_relations = relation_ids + [relation.id]
                    next_evidence = evidence_ids + list(relation.evidence_ids)
                    if relation.object_id in targets and len(next_relations) == 2:
                        evidence = [self.evidence_by_id[item] for item in next_evidence]
                        source_kinds = tuple(dict.fromkeys(item.source_kind for item in evidence))
                        if len(source_kinds) >= 2 and set(source_kinds) >= {"pdf", "python_repo"}:
                            results.append(
                                GraphPath(
                                    derived_id("path", {"relations": next_relations, "evidence": next_evidence}),
                                    tuple(names + [relation.object]),
                                    tuple(next_relations),
                                    tuple(next_evidence),
                                    source_kinds,
                                )
                            )
                    else:
                        queue.append((relation.object_id, next_names, next_relations, next_evidence))
        return results


def detect_relation_conflicts(corpus: Corpus) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
    evidence_by_id = {item.id: item for item in corpus.evidence}
    for relation in corpus.relations:
        grouped[(relation.subject_id, relation.predicate)].append(relation)
    conflicts: list[dict[str, Any]] = []
    for (_subject_id, predicate), relations in grouped.items():
        object_ids = {item.object_id for item in relations}
        if len(object_ids) < 2:
            continue
        sides = []
        source_kinds: set[str] = set()
        for relation in relations:
            if not relation.evidence_ids:
                continue
            ids = [item for item in relation.evidence_ids if item in evidence_by_id]
            if not ids:
                continue
            source_kinds.update(evidence_by_id[item].source_kind for item in ids)
            sides.append(
                {
                    "claim": f"{relation.subject} {relation.predicate} {relation.object}",
                    "evidence_ids": ids,
                    "relation_ids": [relation.id],
                    "source_kind": relation.source_kind,
                }
            )
        if len(sides) >= 2 and len(source_kinds) >= 2:
            conflicts.append({"subject": relations[0].subject, "predicate": predicate, "evidence_sides": sides})
    return conflicts
