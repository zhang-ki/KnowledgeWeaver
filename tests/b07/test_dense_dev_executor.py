from pathlib import Path

import pytest
from pydantic import ValidationError
from test_final_mapping import _fixture
from test_generation_binding import _make_generation

from knowledge_weaver.core.errors import ProviderUnavailable
from knowledge_weaver.evaluation.contracts import DatasetStatus, human_review_digest
from knowledge_weaver.evaluation.dataset import (
    DatasetSummary,
    LoadedDataset,
    load_dataset,
)
from knowledge_weaver.evaluation.executors import (
    PublicPortBenchmarkExecutor,
    _citation_resolutions,
)
from knowledge_weaver.evaluation.generation_binding import verify_generation_manifest
from knowledge_weaver.evaluation.gold_mapping import map_gold_to_generation
from knowledge_weaver.evaluation.protocol import load_protocol
from knowledge_weaver.evaluation.runner import run_dense_dev_slice
from knowledge_weaver.evaluation.runs import (
    AttemptOutcome,
    run_query_with_retries,
)
from knowledge_weaver.evaluation.scheduling import (
    REQUIRED_DEPENDENCIES,
    ExperimentSuite,
    SuiteStatus,
    build_formal_matrix,
)
from knowledge_weaver.evaluation.suite_registry import RouteBinding, build_suite_registry
from knowledge_weaver.models.contracts import (
    CitationCheck,
    IndexChannel,
    LLMResult,
    ModelTokenUsage,
    RetrievalHit,
    RetrievalResult,
    Split,
    Usage,
)
from knowledge_weaver.providers.fake import FakeGenerationProvider
from knowledge_weaver.retrieval.cache_state import (
    CacheStateOperation,
    CacheStateReceipt,
)
from knowledge_weaver.retrieval.indexed import DenseRetriever


class _DensePortSpy(DenseRetriever):
    def __init__(self, hit: RetrievalHit) -> None:
        self.hit = hit
        self.calls = 0
        self.contexts = []

    async def retrieve(self, query):
        self.calls += 1
        self.contexts.append(query.context)
        return RetrievalResult(
            context=query.context,
            hits=[self.hit],
            usage=Usage(elapsed_ms=3, tool_calls=1, count_kind="exact"),
        )


class _MemoryKnowledgeStore:
    def __init__(self, chunks, valid_evidence_ids) -> None:
        self.chunks = {item.id: item for item in chunks}
        self.valid_evidence_ids = valid_evidence_ids
        self.chunk_reads = 0
        self.citation_validation_calls = []

    async def get_chunks(self, ids, context):
        self.chunk_reads += 1
        assert context.source_ids is None
        return [self.chunks[item] for item in ids if item in self.chunks]

    async def validate_citations(self, ids, context):
        assert context.source_ids is None
        self.citation_validation_calls.append(list(ids))
        return CitationCheck(
            valid_ids=[item for item in ids if item in self.valid_evidence_ids],
            invalid=[],
        )


class _RetryOnceGenerationProvider(FakeGenerationProvider):
    def __init__(self, response):
        super().__init__(response)
        self.fail_once = True

    async def generate(self, request):
        if self.fail_once:
            self.calls += 1
            self.fail_once = False
            raise ProviderUnavailable("temporary provider limit", category="rate_limit", retryable=True)
        self.calls += 1
        return LLMResult(
            parsed_json=dict(self.response),
            usage=ModelTokenUsage(input_tokens=7, output_tokens=4, count_kind="exact"),
            model="fixture-model",
            revision="fixture-r1",
        )


class _MissingReceiptController:
    def config_sha256_for(self, route_id):
        return "c" * 64

    async def prepare(self, route_id, context, requested_state):
        return None


class _WrongHashReceiptController:
    def config_sha256_for(self, route_id):
        return "c" * 64

    async def prepare(self, route_id, context, requested_state):
        return CacheStateReceipt(
            route_id=route_id,
            requested_state=requested_state,
            applied_state=requested_state,
            implementation_sha256="d" * 64,
            config_sha256="e" * 64,
            controlled_components=("test.cache",),
            warmup_sequence=("test.cache",),
            operations=(
                CacheStateOperation(
                    sequence=1,
                    component_id="test.cache",
                    operation="reset" if requested_state == "cold" else "warm",
                    verified=True,
                ),
            ),
            complete=True,
        )


class _SpyExecutor(PublicPortBenchmarkExecutor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.blind_queries = []

    async def execute_query(self, query, suite, **kwargs):
        self.blind_queries.append(query)
        return await super().execute_query(query, suite, **kwargs)


def _drive_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("test coroutine unexpectedly yielded to an event loop")


def _dev_suite() -> ExperimentSuite:
    return ExperimentSuite(
        suite_id="dev/dense/standard/seed-17",
        kind="dev",
        system_id="dense",
        budget_id="standard",
        seed=17,
        ablation_id=None,
        status=SuiteStatus.planned,
        blockers=(),
        cache_states=("cold",),
        expected_query_runs=1,
    )


def _dev_inputs():
    prepared, source, document, chunks, evidence, relations = _fixture()
    dev_case = prepared.case.model_copy(update={"split": Split.dev})
    reviewed_at = prepared.human_review.reviewed_at
    updated_review = prepared.human_review.model_copy(
        update={
            "reviewed_record_sha256": human_review_digest(
                dev_case,
                prepared.source_ids,
                prepared.gold_quote_sha256,
                prepared.human_review.reviewer_id,
                reviewed_at,
                prepared.human_review.checklist,
                prepared.gold_paths,
                prepared.source_record_sha256,
            )
        }
    )
    prepared = prepared.model_copy(update={"case": dev_case, "human_review": updated_review})
    root = Path(__file__).resolve().parents[2]
    blocked = load_dataset(root)
    manifest = blocked.manifest.model_copy(
        update={
            "status": DatasetStatus.frozen,
            "case_count": 100,
            "cases_file": "synthetic-cases.jsonl",
            "cases_sha256": "a" * 64,
            "sources": [source],
            "blockers": [],
        }
    )
    dataset = LoadedDataset(
        manifest=manifest,
        cases=(prepared,),
        summary=DatasetSummary(
            case_count=100,
            category_counts={},
            language_counts={},
            split_counts={"train": 30, "dev": 1, "test": 69},
            split_family_counts={"train": 5, "dev": 1, "test": 5},
            unanswerable_count=10,
        ),
    )
    generation, generation_sha256 = _make_generation()
    mapping = map_gold_to_generation(
        [prepared],
        [source],
        [document],
        chunks,
        evidence,
        relations,
        generation=generation,
        dataset_sha256="a" * 64,
    )
    assert mapping.ready
    protocol = load_protocol(root / "benchmarks/configs/b07_protocol_v1.json")
    schedule = build_formal_matrix(
        protocol,
        dataset,
        dependency_commits=REQUIRED_DEPENDENCIES,
        dependencies_integrated=True,
        provider_profiles=("fixture",),
        provider_ready=True,
        budget_approved=True,
        generation_id=generation.id,
        generation_sha256=generation_sha256,
    )
    evidence_ids = [item.id for item in evidence]
    mapped_chunk_id = mapping.span_mappings[0].chunk_ids[0]
    hit = RetrievalHit(
        chunk_id=mapped_chunk_id,
        evidence_ids=evidence_ids[:1],
        score=0.9,
        channel=IndexChannel.dense.value,
        rank=1,
    )
    response = {
        "status": "answered",
        "claims": [{"text": "The source supports this claim.", "evidence_ids": evidence_ids[:1]}],
        "unanswered": [],
    }
    retriever = _DensePortSpy(hit)
    store = _MemoryKnowledgeStore(chunks, set(evidence_ids))
    provider = _RetryOnceGenerationProvider(response)
    generation_config_sha256 = "d" * 64
    route_binding = RouteBinding(
        route_id="retriever.dense",
        implementation_id="retriever.dense",
        config_sha256="c" * 64,
        adapter=retriever,
    )
    registry = build_suite_registry(
        schedule,
        protocol,
        route_bindings={"retriever.dense": route_binding},
        generation_config_sha256=generation_config_sha256,
        dev_dataset_sha256=dataset.loaded_content_sha256,
    )
    executor = _SpyExecutor(
        protocol,
        registry,
        store,
        provider,
        provider_kind="mock",
        provider_profile="in-memory-fixture",
        generation_config_sha256=generation_config_sha256,
    )
    return (
        protocol,
        dataset,
        mapping,
        verify_generation_manifest(generation),
        executor,
        registry,
        retriever,
        store,
        provider,
        schedule,
        prepared,
    )


def test_dense_dev_slice_fails_closed_on_uncontrolled_cache_scope() -> None:
    (
        protocol,
        dataset,
        mapping,
        generation_binding,
        executor,
        registry,
        retriever,
        store,
        provider,
        _schedule,
        _prepared,
    ) = _dev_inputs()

    report = _drive_immediate(
        run_dense_dev_slice(
            protocol,
            dataset,
            mapping,
            executor,
            registry,
            generation_binding=generation_binding,
            retry_delay_seconds=0,
        )
    )

    record = report.query_records[0]
    outcome = record.attempts[-1]
    assert report.status == "DEV_ONLY_PARTIAL"
    assert report.formal is False and report.synthetic_fixture is True
    assert report.cache_state_enforced is False
    assert outcome.status == "failed"
    assert outcome.failure_code == "CACHE_SCOPE_INCOMPLETE"
    assert outcome.cache_state_receipt is not None
    assert outcome.cache_state_receipt.complete is False
    assert any(
        "embedding-provider" in item
        for item in outcome.cache_state_receipt.uncontrolled_components
    )
    assert any(
        "answer-provider" in item
        for item in outcome.cache_state_receipt.uncontrolled_components
    )
    assert len(executor.blind_queries) == 1
    assert set(executor.blind_queries[0].model_fields_set) == {"query_id", "question", "language"}
    assert retriever.calls == 0 and store.chunk_reads == 0 and provider.calls == 0
    assert record.formal_quality_eligible is False
    assert outcome.execution_output is None
    assert outcome.measurements is None


@pytest.mark.parametrize(
    ("controller", "expected_code"),
    [
        (_MissingReceiptController(), "CACHE_RECEIPT_MISSING"),
        (_WrongHashReceiptController(), "CACHE_RECEIPT_CONFIG_MISMATCH"),
    ],
)
def test_missing_or_wrong_cache_receipt_is_preserved_as_runner_failure(
    controller, expected_code
) -> None:
    (
        protocol,
        _dataset,
        _mapping,
        generation_binding,
        _executor,
        registry,
        retriever,
        store,
        provider,
        _schedule,
        prepared,
    ) = _dev_inputs()
    suite = _dev_suite()
    invalid_executor = PublicPortBenchmarkExecutor(
        protocol,
        registry,
        store,
        provider,
        provider_kind="mock",
        provider_profile="in-memory-fixture",
        generation_config_sha256="d" * 64,
    )
    invalid_executor.cache_state_controller.prepare = controller.prepare

    record = _drive_immediate(
        run_query_with_retries(
            invalid_executor,
            prepared,
            suite,
            formal_dataset=False,
            cache_state="cold",
            generation_binding=generation_binding,
            retry_delay_seconds=0,
        )
    )

    assert record.status == "failed"
    assert record.attempts[0].failure_code == expected_code
    if expected_code == "CACHE_RECEIPT_MISSING":
        assert record.attempts[0].cache_state_receipt is None
    else:
        assert record.attempts[0].cache_state_receipt is not None
        assert record.attempts[0].cache_state_receipt.complete is True
    assert retriever.calls == 0


def test_complete_attempt_cannot_omit_cache_state_receipt() -> None:
    with pytest.raises(ValidationError, match="verified cache-state receipt"):
        AttemptOutcome(
            attempt=1,
            status="complete",
            provider_kind="mock",
            provider_profile="fixture",
            model_revision="fixture-v1",
        )


def test_dense_executor_rejects_generator_sha_mismatch_before_any_query() -> None:
    (
        protocol,
        _dataset,
        _mapping,
        _generation_binding,
        _executor,
        registry,
        _retriever,
        store,
        provider,
        _schedule,
        _prepared,
    ) = _dev_inputs()
    with pytest.raises(ValueError, match="generation config SHA"):
        PublicPortBenchmarkExecutor(
            protocol,
            registry,
            store,
            provider,
            provider_kind="mock",
            provider_profile="in-memory-fixture",
            generation_config_sha256="e" * 64,
        )
    with pytest.raises(ValueError, match="fake generation provider cannot be labeled real"):
        PublicPortBenchmarkExecutor(
            protocol,
            registry,
            store,
            provider,
            provider_kind="real",
            provider_profile="mislabelled-fixture",
            generation_config_sha256="d" * 64,
        )


def test_dense_implementation_cannot_be_registered_as_bm25() -> None:
    (
        protocol,
        _dataset,
        _mapping,
        _generation_binding,
        _executor,
        _registry,
        retriever,
        store,
        provider,
        schedule,
        _prepared,
    ) = _dev_inputs()
    mismatched_registry = build_suite_registry(
        schedule,
        protocol,
        route_bindings={
            "retriever.bm25": RouteBinding(
                route_id="retriever.bm25",
                implementation_id="retriever.bm25",
                config_sha256="c" * 64,
                adapter=retriever,
            )
        },
        generation_config_sha256="d" * 64,
    )
    with pytest.raises(ValueError, match="does not match its frozen implementation"):
        PublicPortBenchmarkExecutor(
            protocol,
            mismatched_registry,
            store,
            provider,
            provider_kind="mock",
            provider_profile="in-memory-fixture",
            generation_config_sha256="d" * 64,
        )


def test_invalid_citation_resolution_is_unresolvable_without_gold_access() -> None:
    citation_status = _citation_resolutions(("not-a-uuid",), set())

    assert citation_status[0].evidence_id == "not-a-uuid"
    assert citation_status[0].resolves_to_original is False
