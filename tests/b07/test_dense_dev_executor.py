from pathlib import Path

import pytest
from test_final_mapping import _fixture
from test_generation_binding import _make_generation

from knowledge_weaver.core.errors import ProviderUnavailable
from knowledge_weaver.evaluation.contracts import DatasetStatus, human_review_digest
from knowledge_weaver.evaluation.dataset import (
    DatasetSummary,
    LoadedDataset,
    load_dataset,
)
from knowledge_weaver.evaluation.executors import PublicPortBenchmarkExecutor
from knowledge_weaver.evaluation.generation_binding import verify_generation_manifest
from knowledge_weaver.evaluation.gold_mapping import map_gold_to_generation
from knowledge_weaver.evaluation.protocol import load_protocol
from knowledge_weaver.evaluation.runner import run_dense_dev_slice
from knowledge_weaver.evaluation.scheduling import (
    REQUIRED_DEPENDENCIES,
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


def test_dense_dev_slice_runs_blind_retrieval_and_runner_side_gold_scoring() -> None:
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
    assert report.status == "DEV_ONLY_COMPLETE"
    assert report.formal is False and report.synthetic_fixture is True
    assert report.cache_state_enforced is False
    assert len(record.attempts) == 2
    assert executor.blind_queries[0] == executor.blind_queries[1]
    assert set(executor.blind_queries[0].model_fields_set) == {"query_id", "question", "language"}
    assert retriever.calls == 2 and store.chunk_reads == 2 and provider.calls == 2
    assert retriever.contexts[0].source_ids is None
    assert outcome.execution_output is not None
    assert outcome.execution_output.retrieval_hits[0].chunk_id == mapping.span_mappings[0].chunk_ids[0]
    assert outcome.measurements is not None
    assert outcome.measurements.evidence_recall == 1.0
    assert outcome.measurements.citation_precision is None
    assert outcome.measurements.citation_validity == 1.0
    assert outcome.measurements.answer_correctness is None
    assert record.formal_quality_eligible is False
    assert record.input_tokens == 7 and record.output_tokens == 4
    assert record.llm_calls == 2
    assert not hasattr(outcome.execution_output, "gold")


def test_formal_dense_suite_stays_blocked_without_cache_control_and_bad_config() -> None:
    (
        _protocol,
        _dataset,
        _mapping,
        generation_binding,
        executor,
        _registry,
        retriever,
        _store,
        _provider,
        schedule,
        prepared,
    ) = _dev_inputs()
    formal_suite = next(
        item for item in schedule.suites if item.suite_id == "system/dense/standard/seed-17"
    )
    query = executor.blind_queries[0] if executor.blind_queries else None
    if query is None:
        from knowledge_weaver.evaluation.runs import BlindBenchmarkInput

        query = BlindBenchmarkInput(
            query_id=prepared.case.id,
            question=prepared.case.question,
            language=prepared.case.language,
        )
    with pytest.raises(ValueError, match="CACHE_CONTROL_PORT"):
        _drive_immediate(
            executor.execute_query(
                query,
                formal_suite,
                attempt=1,
                cache_state="cold",
                generation=generation_binding.generation,
                generation_sha256=generation_binding.generation_sha256,
            )
        )
    assert retriever.calls == 0


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


def test_invalid_model_citation_is_retained_as_unresolvable_without_gold_access() -> None:
    (
        protocol,
        dataset,
        mapping,
        generation_binding,
        executor,
        registry,
        _retriever,
        store,
        provider,
        _schedule,
        _prepared,
    ) = _dev_inputs()
    provider.response["claims"][0]["evidence_ids"] = ["not-a-uuid"]

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

    output = report.query_records[0].attempts[-1].execution_output
    assert output is not None
    assert output.answer.claims[0].evidence_ids == ("not-a-uuid",)
    assert output.citations[0].resolves_to_original is False
    assert store.citation_validation_calls == []
    assert report.query_records[0].measurements is not None
    assert report.query_records[0].measurements.citation_validity == 0.0
