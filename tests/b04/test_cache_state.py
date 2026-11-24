from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from knowledge_weaver.graph.retriever import GraphRetriever
from knowledge_weaver.models import GraphSnapshot, QueryContext
from knowledge_weaver.retrieval.cache_state import (
    CacheRouteProfile,
    CacheStateController,
)


def _context() -> QueryContext:
    return QueryContext(
        corpus_id="00000000-0000-4000-8000-000000000001",
        generation_id="00000000-0000-4000-8000-000000000002",
        request_id="00000000-0000-4000-8000-000000000003",
        deadline_utc=datetime.now(UTC) + timedelta(seconds=30),
    )


class _MemoryParticipant:
    def __init__(self, component_id: str, calls: list[str]) -> None:
        self.component_id = component_id
        self.implementation_sha256 = "a" * 64
        self.config_sha256 = "b" * 64
        self.calls = calls
        self.cached_context_ids: set[str] = {"stale"}

    async def reset_cache(self) -> bool:
        self.calls.append(f"reset:{self.component_id}")
        self.cached_context_ids.clear()
        return not self.cached_context_ids

    async def warm_cache(self, context: QueryContext) -> bool:
        self.calls.append(f"warm:{self.component_id}")
        self.cached_context_ids.add(context.generation_id)
        return context.generation_id in self.cached_context_ids


class _MemoryGraphStore:
    def __init__(self) -> None:
        self.snapshot_calls = 0

    async def snapshot(self, context: QueryContext) -> GraphSnapshot:
        self.snapshot_calls += 1
        return GraphSnapshot(
            context=context,
            entities=[],
            mentions=[],
            relations=[],
            evidence=[],
            provenance=[],
            snapshot_hash=hashlib.sha256(b"empty-graph").hexdigest(),
        )


@pytest.mark.asyncio
async def test_cold_reset_is_repeatable_and_warmup_uses_frozen_sorted_sequence() -> None:
    calls: list[str] = []
    route_id = "retriever.test"
    controller = CacheStateController(
        {
            route_id: CacheRouteProfile(
                route_id=route_id,
                participants=(
                    _MemoryParticipant("cache.z", calls),
                    _MemoryParticipant("cache.a", calls),
                ),
                uncontrolled_components=(),
            )
        }
    )
    context = _context()

    first_cold = await controller.prepare(route_id, context, "cold")
    second_cold = await controller.prepare(route_id, context, "cold")
    warm = await controller.prepare(route_id, context, "warm")

    assert first_cold.complete and first_cold.applied_state == "cold"
    assert second_cold.complete and second_cold.applied_state == "cold"
    assert warm.complete and warm.applied_state == "warm"
    assert warm.warmup_sequence == ("cache.a", "cache.z")
    assert [item.operation for item in warm.operations] == ["warm", "warm"]
    assert calls == [
        "reset:cache.a",
        "reset:cache.z",
        "reset:cache.a",
        "reset:cache.z",
        "warm:cache.a",
        "warm:cache.z",
    ]
    assert len(first_cold.config_sha256) == 64
    assert first_cold.config_sha256 == warm.config_sha256
    assert len(warm.implementation_sha256) == 64


@pytest.mark.asyncio
async def test_graph_snapshot_cache_can_be_cleared_and_preheated_but_external_scope_stays_partial() -> None:
    context = _context()
    graph_store = _MemoryGraphStore()
    retriever = GraphRetriever(graph_store, object(), object())
    controller = CacheStateController.for_retrievers({"retriever.graph": retriever})

    warm = await controller.prepare("retriever.graph", context, "warm")
    assert warm.applied_state == "warm" and not warm.complete
    assert retriever.snapshot_cache_size == 1
    cold = await controller.prepare("retriever.graph", context, "cold")
    assert cold.applied_state == "cold" and not cold.complete
    assert cold.operations[0].verified is True
    assert retriever.snapshot_cache_size == 0
    cold_again = await controller.prepare("retriever.graph", context, "cold")
    assert cold_again.applied_state == "cold" and not cold_again.complete
    assert cold_again.operations[0].verified is True
    assert retriever.snapshot_cache_size == 0
    warm_again = await controller.prepare("retriever.graph", context, "warm")

    assert warm_again.applied_state == "warm" and not warm_again.complete
    assert warm.warmup_sequence == ("retriever.graph/graph-snapshot-cache",)
    assert warm.operations[0].verified is True
    assert warm_again.operations[0].verified is True
    assert retriever.snapshot_cache_size == 1
    assert graph_store.snapshot_calls == 2
    assert set(warm.uncontrolled_components) == {
        "retriever.graph/entity-index/storage-cache",
        "retriever.graph/graph-store/backend-cache",
        "retriever.graph/knowledge-store/backend-cache",
    }


@pytest.mark.asyncio
async def test_failed_participant_verification_does_not_claim_applied_state() -> None:
    class _FailingParticipant(_MemoryParticipant):
        async def reset_cache(self) -> bool:
            self.calls.append(f"reset:{self.component_id}")
            return False

    calls: list[str] = []
    route_id = "retriever.test"
    controller = CacheStateController(
        {
            route_id: CacheRouteProfile(
                route_id=route_id,
                participants=(_FailingParticipant("cache.a", calls),),
                uncontrolled_components=(),
            )
        }
    )

    receipt = await controller.prepare(route_id, _context(), "cold")

    assert receipt.complete is False
    assert receipt.applied_state is None
    assert receipt.operations[0].verified is False
    assert receipt.operations[0].failure_type == "StateVerificationFailed"
