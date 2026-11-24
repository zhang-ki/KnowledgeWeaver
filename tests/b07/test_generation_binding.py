from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

import pytest
from test_final_mapping import _fixture

from knowledge_weaver.evaluation.generation_binding import (
    GenerationBindingError,
    verify_generation_manifest,
)
from knowledge_weaver.evaluation.runs import (
    AttemptOutcome,
    BlindBenchmarkInput,
    QueryMeasurements,
    run_query_with_retries,
)
from knowledge_weaver.evaluation.scheduling import (
    ExperimentSuite,
    SuiteStatus,
    valid_generation_binding,
)
from knowledge_weaver.models.contracts import Generation, GenerationState
from knowledge_weaver.retrieval.cache_state import CacheStateOperation, CacheStateReceipt


def _cache_receipt(cache_state: Literal["cold", "warm"]) -> CacheStateReceipt:
    return CacheStateReceipt(
        route_id="retriever.test",
        requested_state=cache_state,
        applied_state=cache_state,
        implementation_sha256="a" * 64,
        config_sha256="b" * 64,
        controlled_components=("test.cache",),
        warmup_sequence=("test.cache",),
        operations=(
            CacheStateOperation(
                sequence=1,
                component_id="test.cache",
                operation="reset" if cache_state == "cold" else "warm",
                verified=True,
            ),
        ),
        complete=True,
    )


def _make_generation() -> tuple[Generation, str]:
    _case, _source, document, chunks, evidence, relations = _fixture()
    generation_id = "11111111-1111-4111-8111-111111111111"
    corpus_id = "22222222-2222-4222-8222-222222222222"
    source_version_ids = [
        "44444444-4444-4444-8444-444444444444",
        "33333333-3333-4333-8333-333333333333",
    ]
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
        kind: hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()
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


def _with_source_version_membership(
    generation: Generation,
    source_version_ids: list[str],
    *,
    include_source_version_hash: bool = True,
    rebind_source_version_hash: bool = True,
) -> Generation:
    manifest = dict(generation.index_manifest)
    member_hashes = dict(manifest["member_hashes"])
    if not include_source_version_hash:
        member_hashes.pop("source_version", None)
    elif rebind_source_version_hash:
        member_hashes["source_version"] = hashlib.sha256(
            "\n".join(sorted(source_version_ids)).encode("utf-8")
        ).hexdigest()
    manifest["member_hashes"] = member_hashes
    payload = {
        "source_version_ids": source_version_ids,
        "member_hashes": member_hashes,
        "index_manifest": {
            key: value
            for key, value in manifest.items()
            if key != "generation_hash"
        },
    }
    manifest["generation_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return generation.model_copy(
        update={"source_version_ids": source_version_ids, "index_manifest": manifest}
    )


def _rebind_generation_manifest(
    generation: Generation, manifest: dict[str, object]
) -> Generation:
    manifest = dict(manifest)
    manifest.pop("generation_hash", None)
    member_hashes = manifest["member_hashes"]
    payload = {
        "source_version_ids": generation.source_version_ids,
        "member_hashes": member_hashes,
        "index_manifest": manifest,
    }
    manifest["generation_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return generation.model_copy(update={"index_manifest": manifest})


def _with_empty_entity_relation_members(generation: Generation) -> Generation:
    manifest = dict(generation.index_manifest)
    member_hashes = dict(manifest["member_hashes"])
    empty_hash = hashlib.sha256(b"").hexdigest()
    member_hashes["entity"] = empty_hash
    member_hashes["relation"] = empty_hash
    manifest["member_hashes"] = member_hashes
    empty_generation = generation.model_copy(
        update={"entity_ids": [], "relation_ids": []}
    )
    return _rebind_generation_manifest(empty_generation, manifest)


def test_source_version_membership_verifies_with_publisher_sorting() -> None:
    generation, _ = _make_generation()

    verified = verify_generation_manifest(generation)

    assert verified.generation_sha256 == generation.index_manifest["generation_hash"]
    assert generation.source_version_ids != sorted(generation.source_version_ids)


def test_empty_entity_relation_members_require_explicit_canonical_hashes() -> None:
    generation = _with_empty_entity_relation_members(_make_generation()[0])
    empty_hash = hashlib.sha256(b"").hexdigest()

    verified = verify_generation_manifest(generation)

    assert generation.index_manifest["member_hashes"]["entity"] == empty_hash
    assert generation.index_manifest["member_hashes"]["relation"] == empty_hash
    assert verified.generation_sha256 == generation.index_manifest["generation_hash"]

    for kind in ("entity", "relation"):
        manifest = dict(generation.index_manifest)
        member_hashes = dict(manifest["member_hashes"])
        member_hashes.pop(kind)
        manifest["member_hashes"] = member_hashes
        malformed = _rebind_generation_manifest(generation, manifest)
        with pytest.raises(GenerationBindingError, match="membership"):
            verify_generation_manifest(malformed)


def test_source_version_membership_requires_hash() -> None:
    generation, _ = _make_generation()
    malformed = _with_source_version_membership(
        generation,
        generation.source_version_ids,
        include_source_version_hash=False,
    )

    with pytest.raises(GenerationBindingError, match="membership"):
        verify_generation_manifest(malformed)


def test_source_version_membership_must_match_persisted_ids() -> None:
    generation, _ = _make_generation()
    changed_ids = [
        "55555555-5555-4555-8555-555555555555",
        generation.source_version_ids[1],
    ]
    malformed = _with_source_version_membership(
        generation,
        changed_ids,
        rebind_source_version_hash=False,
    )

    with pytest.raises(GenerationBindingError, match="membership"):
        verify_generation_manifest(malformed)


def test_source_version_ids_must_follow_uuid_text_contract() -> None:
    generation, _ = _make_generation()
    malformed = generation.model_copy(
        update={"source_version_ids": ["not-a-uuid-text"]}
    )

    with pytest.raises(GenerationBindingError, match="UUIDText"):
        verify_generation_manifest(malformed)


def test_observed_source_version_ids_cannot_replace_persisted_members() -> None:
    generation, _ = _make_generation()

    with pytest.raises(GenerationBindingError, match="source-version membership"):
        verify_generation_manifest(
            generation,
            observed_member_ids={
                "source_version": ["55555555-5555-4555-8555-555555555555"]
            },
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
        self.query: BlindBenchmarkInput | None = None
        self.generation: Generation | None = None
        self.generation_sha256: str | None = None

    async def execute_query(
        self,
        query: BlindBenchmarkInput,
        suite,
        *,
        attempt: int,
        cache_state: Literal["cold", "warm"],
        generation: Generation,
        generation_sha256: str,
    ) -> AttemptOutcome:
        self.calls += 1
        self.query = query
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
            cache_state_receipt=_cache_receipt(cache_state),
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
    assert executor.query is not None
    assert executor.query.model_dump() == {
        "query_id": case.case.id,
        "question": case.case.question,
        "language": case.case.language,
    }
    assert set(executor.query.model_dump()) == {"query_id", "question", "language"}
    with pytest.raises(ValueError):
        executor.query.question = "mutated"  # type: ignore[misc]
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
