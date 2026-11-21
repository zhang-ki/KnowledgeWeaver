from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

import pytest
from test_final_mapping import _fixture

from knowledge_weaver.evaluation.runs import (
    AttemptOutcome,
    QueryMeasurements,
    run_query_with_retries,
)
from knowledge_weaver.evaluation.scheduling import (
    ExperimentSuite,
    SuiteStatus,
    valid_generation_binding,
)
from knowledge_weaver.models.contracts import Generation, GenerationState


def _make_generation() -> tuple[Generation, str]:
    _case, _source, document, chunks, evidence, relations = _fixture()
    generation_id = "11111111-1111-4111-8111-111111111111"
    corpus_id = "22222222-2222-4222-8222-222222222222"
    source_version_ids = ["33333333-3333-4333-8333-333333333333"]
    member_ids = {
        "source_version": source_version_ids,
        "document": [document.id],
        "chunk": sorted(item.id for item in chunks),
        "evidence": sorted(item.id for item in evidence),
        "provenance": sorted({item.provenance_id for item in evidence}),
        "entity": sorted(
            {entity_id for item in relations for entity_id in (item.subject_id, item.object_id)}
        ),
        "mention": [],
        "relation": sorted(item.id for item in relations),
    }
    member_hashes = {
        kind: hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
        for kind, ids in member_ids.items()
    }
    manifest: dict[str, object] = {
        "member_hashes": member_hashes,
        "indexes": {"fixture": "generation-binding-test"},
    }
    generation_sha256 = hashlib.sha256(
        json.dumps(
            {
                "source_version_ids": source_version_ids,
                "member_hashes": member_hashes,
                "index_manifest": manifest,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    manifest["generation_hash"] = generation_sha256
    return (
        Generation(
            id=generation_id,
            corpus_id=corpus_id,
            state=GenerationState.READY,
            source_version_ids=source_version_ids,
            chunk_ids=member_ids["chunk"],
            entity_ids=member_ids["entity"],
            relation_ids=member_ids["relation"],
            index_manifest=manifest,
            embedding_fingerprint="fixture-generation-binding-v1",
            published_at=datetime.now(UTC),
        ),
        generation_sha256,
    )


class _GenerationStore:
    def __init__(self, generation: Generation) -> None:
        self.generation_record = generation
        self.calls = 0

    async def generation(self, generation_id: str) -> Generation:
        self.calls += 1
        if generation_id != self.generation_record.id:
            raise KeyError(generation_id)
        return self.generation_record


class _IdentityExecutor:
    provider_kind = "synthetic"

    def __init__(self) -> None:
        self.calls = 0
        self.generation: Generation | None = None
        self.generation_sha256: str | None = None

    async def execute_query(
        self,
        case,
        suite,
        *,
        attempt: int,
        cache_state: Literal["cold", "warm"],
        generation: Generation,
        generation_sha256: str,
    ) -> AttemptOutcome:
        self.calls += 1
        self.generation = generation
        self.generation_sha256 = generation_sha256
        return AttemptOutcome(
            attempt=attempt,
            status="complete",
            retryable=False,
            provider_kind="synthetic",
            provider_profile="fixture",
            model_revision="fixture-v1",
            count_kind="unavailable",
            cache_state=cache_state,
            measurements=QueryMeasurements(),
            execution_generation_id=generation.id,
            execution_generation_sha256=generation_sha256,
        )


def _suite() -> ExperimentSuite:
    return ExperimentSuite(
        suite_id="suite-generation-binding",
        kind="system",
        system_id="fixed_agent",
        budget_id="low",
        seed=17,
        ablation_id=None,
        status=SuiteStatus.planned,
        cache_states=("cold",),
        expected_query_runs=1,
    )


def _drive_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("test coroutine unexpectedly yielded to an event loop")


def test_generation_id_requires_the_uuid_text_contract() -> None:
    assert not valid_generation_binding("generation-expected", "a" * 64)


def test_executor_receives_resolved_generation_and_content_digest() -> None:
    case, *_ = _fixture()
    generation, generation_sha256 = _make_generation()
    store = _GenerationStore(generation)
    executor = _IdentityExecutor()

    record = _drive_immediate(
        run_query_with_retries(
            executor,
            case,
            _suite(),
            formal_dataset=False,
            generation_id=generation.id,
            generation_sha256=generation_sha256,
            generation_resolver=store,
            retry_delay_seconds=0,
        )
    )

    assert store.calls == 1
    assert executor.calls == 1
    assert executor.generation is generation
    assert executor.generation_sha256 == generation_sha256
    assert record.generation_id == generation.id
    assert record.generation_sha256 == generation_sha256


@pytest.mark.parametrize("failure", ["malformed_id", "missing", "digest_mismatch", "tampered_manifest"])
def test_unresolved_or_mismatched_generation_blocks_before_executor(failure: str) -> None:
    case, *_ = _fixture()
    generation, generation_sha256 = _make_generation()
    if failure == "tampered_manifest":
        generation = generation.model_copy(
            update={
                "index_manifest": {
                    **generation.index_manifest,
                    "indexes": {"fixture": "tampered"},
                }
            }
        )
    store = _GenerationStore(generation)
    executor = _IdentityExecutor()
    requested_id = generation.id
    if failure == "malformed_id":
        requested_id = "generation-expected"
    elif failure == "missing":
        requested_id = "44444444-4444-4444-8444-444444444444"
    requested_sha256 = "f" * 64 if failure == "digest_mismatch" else generation_sha256

    with pytest.raises(ValueError, match="[Gg]eneration"):
        _drive_immediate(
            run_query_with_retries(
                executor,
                case,
                _suite(),
                formal_dataset=False,
                generation_id=requested_id,
                generation_sha256=requested_sha256,
                generation_resolver=store,
                retry_delay_seconds=0,
            )
        )

    assert executor.calls == 0
    if failure == "malformed_id":
        assert store.calls == 0
