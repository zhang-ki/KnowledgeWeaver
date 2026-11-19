from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from knowledge_weaver.models import (
    IndexCandidate,
    IndexChannel,
    IndexQuery,
    ScoreDirection,
)
from knowledge_weaver.ports import EmbeddingProvider


class QdrantDenseIndex:
    """Qdrant adapter. PostgreSQL remains authoritative; points are generation scoped."""
    def __init__(
        self,
        url: str,
        provider: EmbeddingProvider,
        *,
        api_key: str | None = None,
        collection_prefix: str = "kw",
    ) -> None:
        self.client = AsyncQdrantClient(url=url, api_key=api_key)
        self.provider = provider
        self.collection_prefix = collection_prefix
        self._metadata: dict[tuple[str, str], dict[str, Any]] = {}

    def collection(self, corpus_id: str) -> str:
        return f"{self.collection_prefix}_{hashlib.sha256(corpus_id.encode()).hexdigest()[:24]}"

    async def publish(self, corpus_id: str, generation_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        texts = [row["text"] for row in rows]
        if not texts:
            self._metadata[(corpus_id, generation_id)] = {"dimension": None, "model": None, "revision": None, "fingerprint": None}
            return self._metadata[(corpus_id, generation_id)]
        batch = await self.provider.embed(texts, "document")
        if len(batch.vectors) != len(rows):
            raise ValueError("embedding count does not match chunks")
        collection = self.collection(corpus_id)
        if not await self.client.collection_exists(collection):
            await self.client.create_collection(collection, vectors_config=models.VectorParams(size=batch.dimension, distance=models.Distance.COSINE))
        info = await self.client.get_collection(collection)
        vector_config = info.config.params.vectors
        if isinstance(vector_config, dict):
            vector_config = vector_config.get("")
        if vector_config is None or vector_config.size != batch.dimension:
            raise ValueError("Qdrant collection dimension mismatch")
        points = []
        for row, vector in zip(rows, batch.vectors, strict=True):
            point_id = str(uuid.uuid5(uuid.UUID(generation_id), json.dumps([row["chunk_id"], batch.fingerprint], separators=(",", ":"))))
            points.append(models.PointStruct(id=point_id, vector=vector, payload={"corpus_id": corpus_id, "generation_id": generation_id, "source_id": row["source_id"], "chunk_id": row["chunk_id"], "embedding_fingerprint": batch.fingerprint, "embedding_mode": "document", "normalized": batch.normalized, "model": batch.model, "revision": batch.revision, "dimension": batch.dimension}))
        await self.client.upsert(collection, points, wait=True)
        meta = {"dimension": batch.dimension, "model": batch.model, "revision": batch.revision, "fingerprint": batch.fingerprint, "collection": collection}
        self._metadata[(corpus_id, generation_id)] = meta
        return meta

    async def search(self, query: IndexQuery) -> list[IndexCandidate]:
        if query.channel != IndexChannel.dense:
            raise ValueError("dense index only accepts the dense channel")
        if query.context.source_ids == []:
            return []
        batch = await self.provider.embed([query.text], "query")
        collection = self.collection(query.context.corpus_id)
        if not await self.client.collection_exists(collection):
            return []
        conditions: list[Any] = [
            models.FieldCondition(key="corpus_id", match=models.MatchValue(value=query.context.corpus_id)),
            models.FieldCondition(key="generation_id", match=models.MatchValue(value=query.context.generation_id)),
        ]
        if query.context.source_ids is not None:
            conditions.append(models.FieldCondition(key="source_id", match=models.MatchAny(any=query.context.source_ids)))
        payload_filter = models.Filter(must=conditions)
        points = await self.client.query_points(collection, query=batch.vectors[0], query_filter=payload_filter, limit=query.top_k, with_payload=True)
        scores = []
        for point in points.points:
            payload = point.payload or {}
            if payload.get("embedding_mode") != "document" or payload.get("dimension") != batch.dimension or payload.get("normalized") != batch.normalized or payload.get("model") != batch.model or payload.get("revision") != batch.revision:
                raise ValueError("query embedding does not match published document embedding fingerprint")
            scores.append((payload["chunk_id"], float(point.score)))
        return [IndexCandidate(chunk_id=chunk_id, rank=i, raw_score=score, score_direction=ScoreDirection.higher) for i, (chunk_id, score) in enumerate(scores, 1)]

    async def delete_generation(self, corpus_id: str, generation_id: str) -> None:
        collection = self.collection(corpus_id)
        if await self.client.collection_exists(collection):
            await self.client.delete(collection, points_selector=models.FilterSelector(filter=models.Filter(must=[models.FieldCondition(key="generation_id", match=models.MatchValue(value=generation_id))])), wait=True)

    async def close(self) -> None:
        await self.client.close()
