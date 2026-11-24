from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_final_mapping import (
    DATA_RELATION_ID,
    _fixture,
    _generation_for,
    _semantic_expectation,
    _semantic_kwargs,
    _with_gold_relation_id,
)
from test_generation_binding import _GenerationStore

from knowledge_weaver.evaluation.contracts import (
    DatasetStatus,
    GoldRelationExpectationManifest,
)
from knowledge_weaver.evaluation.dataset import DatasetSummary, LoadedDataset, load_dataset
from knowledge_weaver.evaluation.generation_binding import verify_generation_manifest
from knowledge_weaver.evaluation.persisted_projection import (
    PersistedGoldProjection,
    PostgresGoldProjectionLookup,
)
from knowledge_weaver.evaluation.protocol import load_protocol
from knowledge_weaver.evaluation.runner import run_formal_matrix
from knowledge_weaver.evaluation.scheduling import (
    REQUIRED_DEPENDENCIES,
    SuiteStatus,
    build_formal_matrix,
)
from knowledge_weaver.models.contracts import Entity, EntityKind, RelationPredicate


class _ProjectionLookup:
    def __init__(self, projection: PersistedGoldProjection) -> None:
        self.projection = projection
        self.generation_ids: list[str] = []

    async def load_gold_projection(self, generation_id: str) -> PersistedGoldProjection:
        self.generation_ids.append(generation_id)
        return self.projection


class _NoCallExecutor:
    provider_kind = "synthetic"

    async def execute_query(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("formal executor must remain behind the provider gate")


def _semantic_run_inputs() -> tuple[LoadedDataset, Any, PersistedGoldProjection]:
    prepared, source_asset, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    relation = relations[0].model_copy(update={"predicate": RelationPredicate.USES})
    subject = Entity(
        id=relation.subject_id,
        corpus_id="00000000-0000-4000-8000-000000000001",
        kind=EntityKind.concept,
        canonical_name="Config.get_loop_factory",
        aliases=["Config.get_loop_factory"],
        namespace="uvicorn",
        identity_key="uvicorn:get_loop_factory",
    )
    object_ = Entity(
        id=relation.object_id,
        corpus_id="00000000-0000-4000-8000-000000000001",
        kind=EntityKind.concept,
        canonical_name="None loop factory",
        namespace="uvicorn",
        identity_key="uvicorn:none_loop_factory",
    )
    expectation = _semantic_expectation(DATA_RELATION_ID)
    semantic_rows = _semantic_kwargs(
        source_asset, document, evidence, [subject, object_], expectation.evidence_basis
    )
    generation = _generation_for(
        document,
        chunks,
        evidence,
        [relation],
        mentions=semantic_rows["mentions"],
        provenance=semantic_rows["provenance"],
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)

    root = Path(__file__).resolve().parents[2]
    blocked_manifest = load_dataset(root).manifest
    cases_sha256 = "a" * 64
    expectation_sidecar = GoldRelationExpectationManifest(
        dataset_id=blocked_manifest.dataset_id,
        dataset_version=blocked_manifest.dataset_version,
        expectations=[expectation],
    )
    expectation_bytes = expectation_sidecar.model_dump_json().encode("utf-8")
    manifest = blocked_manifest.model_copy(
        update={
            "status": DatasetStatus.frozen,
            "case_count": 100,
            "cases_file": "synthetic-cases.jsonl",
            "cases_sha256": cases_sha256,
            "relation_expectations_file": "expectations.json",
            "relation_expectations_sha256": hashlib.sha256(expectation_bytes).hexdigest(),
            "sources": [source_asset],
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
            split_counts={"train": 30, "dev": 20, "test": 50},
            split_family_counts={"train": 5, "dev": 5, "test": 5},
            unanswerable_count=10,
        ),
        relation_expectations={DATA_RELATION_ID: expectation},
        manifest_sha256=hashlib.sha256(
            json.dumps(
                manifest.model_dump(mode="json"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest(),
    )
    projection = PersistedGoldProjection(
        generation=generation,
        sources=(semantic_rows["sources"][0],),
        source_versions=(semantic_rows["source_versions"][0],),
        documents=(document,),
        chunks=tuple(chunks),
        evidence=tuple(evidence),
        entities=(subject, object_),
        mentions=tuple(semantic_rows["mentions"]),
        provenance=tuple(semantic_rows["provenance"]),
        relations=(relation,),
    )
    return dataset, load_protocol(root / "benchmarks/configs/b07_protocol_v1.json"), projection


def _schedule(dataset: LoadedDataset, protocol: Any, projection: PersistedGoldProjection):
    generation_sha256 = verify_generation_manifest(projection.generation).generation_sha256
    return build_formal_matrix(
        protocol,
        dataset,
        dependency_commits=REQUIRED_DEPENDENCIES,
        dependencies_integrated=True,
        provider_profiles=("deepseek", "qwen-embedding"),
        provider_ready=True,
        budget_approved=True,
        policy_artifact_ready=True,
        policy_artifact_sha256="b" * 64,
        generation_id=projection.generation.id,
        generation_sha256=generation_sha256,
    )


class _ScalarResult:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def all(self) -> list[Any]:
        return self.values


class _Session:
    def __init__(self, results: list[list[Any]]) -> None:
        self.results = results
        self.calls = 0

    async def scalars(self, _statement: Any) -> _ScalarResult:
        values = self.results[self.calls]
        self.calls += 1
        return _ScalarResult(values)


class _SessionContext:
    def __init__(self, session: _Session) -> None:
        self.value = session

    async def __aenter__(self) -> _Session:
        return self.value

    async def __aexit__(self, *args: object) -> None:
        return None


class _Database:
    def __init__(self, results: list[list[Any]]) -> None:
        self.value = _Session(results)

    def session(self) -> _SessionContext:
        return _SessionContext(self.value)


class _PersistedStore(_GenerationStore):
    def __init__(self, generation, database: _Database) -> None:
        super().__init__(generation)
        self.database = database
        self.generation_ids: list[str] = []

    async def generation(self, generation_id: str):
        self.generation_ids.append(generation_id)
        return await super().generation(generation_id)


def _database_for_projection(projection: PersistedGoldProjection) -> _Database:
    generation = projection.generation
    member_hashes = generation.index_manifest["member_hashes"]
    assert isinstance(member_hashes, dict)
    members = [
        list(generation.source_version_ids),
        [item.id for item in projection.documents],
        list(generation.chunk_ids),
        [item.id for item in projection.evidence],
        [item.id for item in projection.provenance],
        list(generation.entity_ids),
        [item.id for item in projection.mentions],
        list(generation.relation_ids),
    ]
    version_rows = [
        SimpleNamespace(
            id=item.id,
            source_id=item.source_id,
            manifest_hash=item.manifest_hash,
            revision=item.revision,
            dirty=item.dirty,
            files=[file.model_dump(mode="json") for file in item.files],
        )
        for item in projection.source_versions
    ]
    source_rows = [
        SimpleNamespace(
            id=item.id,
            corpus_id=item.corpus_id,
            kind=item.kind.value,
            logical_uri=item.logical_uri,
            display_name=item.display_name,
            license=item.license,
            created_at=item.created_at,
        )
        for item in projection.sources
    ]
    document_rows = [
        SimpleNamespace(
            id=item.id,
            source_id=item.source_id,
            relative_path=item.relative_path,
            raw_hash=item.raw_hash,
            parser_fingerprint=item.parser_fingerprint,
            normalized_text=item.normalized_text,
            blocks=[block.model_dump(mode="json") for block in item.blocks],
            warnings=item.warnings,
        )
        for item in projection.documents
    ]
    chunk_rows = [
        SimpleNamespace(
            id=item.id,
            document_id=item.document_id,
            parent_id=item.parent_id,
            is_leaf=item.is_leaf,
            text=item.text,
            text_hash=item.text_hash,
            payload=item.model_dump(mode="json"),
        )
        for item in projection.chunks
    ]
    evidence_rows = [
        SimpleNamespace(
            id=item.id,
            chunk_id=item.chunk_id,
            provenance_id=item.provenance_id,
            start=item.start,
            end=item.end,
            quote=item.quote,
            payload=item.model_dump(mode="json"),
        )
        for item in projection.evidence
    ]
    provenance_rows = [
        SimpleNamespace(
            id=item.id,
            source_id=item.source_id,
            document_id=item.document_id,
            method=item.method.value,
            payload=item.model_dump(mode="json"),
        )
        for item in projection.provenance
    ]
    entity_rows = [
        SimpleNamespace(
            id=item.id,
            corpus_id=item.corpus_id,
            kind=item.kind.value,
            namespace=item.namespace,
            identity_key=item.identity_key,
            payload=item.model_dump(mode="json"),
        )
        for item in projection.entities
    ]
    mention_rows = [
        SimpleNamespace(
            id=item.id,
            entity_id=item.entity_id,
            evidence_id=item.evidence_id,
            surface_form=item.surface_form,
            payload=item.model_dump(mode="json"),
        )
        for item in projection.mentions
    ]
    relation_rows = [
        SimpleNamespace(
            id=item.id,
            subject_id=item.subject_id,
            object_id=item.object_id,
            provenance_id=item.provenance_id,
            predicate=item.predicate.value,
            payload=item.model_dump(mode="json"),
        )
        for item in projection.relations
    ]
    relation_evidence_rows = [
        SimpleNamespace(relation_id=item.id, evidence_id=evidence_id)
        for item in projection.relations
        for evidence_id in item.evidence_ids
    ]
    source_version_document_rows = [
        SimpleNamespace(source_version_id=item.id, document_id=file.document_id)
        for item in projection.source_versions
        for file in item.files
    ]
    return _Database(
        members
        + [
            version_rows,
            source_rows,
            document_rows,
            chunk_rows,
            evidence_rows,
            provenance_rows,
            entity_rows,
            mention_rows,
            relation_rows,
            relation_evidence_rows,
            source_version_document_rows,
        ]
    )


def _drive_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("test coroutine unexpectedly yielded to an event loop")


def test_formal_entry_loads_hash_bound_semantics_from_persisted_projection() -> None:
    dataset, protocol, projection = _semantic_run_inputs()
    schedule = _schedule(dataset, protocol, projection)
    store = _PersistedStore(projection.generation, _database_for_projection(projection))
    expected_sha256 = verify_generation_manifest(projection.generation).generation_sha256

    report = _drive_immediate(
        run_formal_matrix(
            schedule,
            protocol,
            dataset,
            _NoCallExecutor(),
            generation_resolver=store,
            policy_training_summary={"formal_ready": True, "synthetic_fixture": False},
            retry_delay_seconds=0,
        )
    )

    assert store.generation_ids == [projection.generation.id, projection.generation.id]
    assert store.database.value.calls == 19
    assert dataset.manifest.relation_expectations_sha256 == hashlib.sha256(
        GoldRelationExpectationManifest(
            dataset_id=dataset.manifest.dataset_id,
            dataset_version=dataset.manifest.dataset_version,
            expectations=list(dataset.relation_expectations.values()),
        ).model_dump_json().encode("utf-8")
    ).hexdigest()
    assert expected_sha256 == projection.generation.index_manifest["generation_hash"]
    assert report.status is SuiteStatus.blocked
    assert any(item.startswith("BLOCKED_PROVIDER:") for item in report.blockers)
    assert not report.query_records
    assert report.to_dict()["relation_expectations_sha256"] == dataset.manifest.relation_expectations_sha256
    assert report.to_dict()["dataset_manifest_sha256"] == dataset.manifest_sha256
    assert report.to_dict()["dataset_content_sha256"] == dataset.loaded_content_sha256
    assert report.to_dict()["required_relation_ids"] == [DATA_RELATION_ID]
    assert report.to_dict()["required_relation_ids_sha256"] == dataset.required_relation_ids_sha256
    assert report.to_dict()["required_relation_queue_sha256"] == dataset.required_relation_queue_sha256


def test_postgres_projection_requires_exact_persisted_member_rows() -> None:
    _dataset, _protocol, projection = _semantic_run_inputs()
    database = _database_for_projection(projection)
    database.value.results[8] = []
    store = _PersistedStore(projection.generation, database)

    with pytest.raises(ValueError, match="SourceVersion rows are incomplete"):
        _drive_immediate(PostgresGoldProjectionLookup(store).load_gold_projection(projection.generation.id))


@pytest.mark.parametrize("missing", ["source_versions", "mentions", "provenance"])
def test_formal_entry_fails_closed_on_incomplete_persisted_semantics(missing: str) -> None:
    dataset, protocol, projection = _semantic_run_inputs()
    schedule = _schedule(dataset, protocol, projection)
    incomplete_projection = replace(projection, **{missing: ()})
    lookup = _ProjectionLookup(incomplete_projection)

    report = _drive_immediate(
        run_formal_matrix(
            schedule,
            protocol,
            dataset,
            _NoCallExecutor(),
            generation_resolver=_GenerationStore(projection.generation),
            gold_projection_lookup=lookup,
            policy_training_summary={"formal_ready": True, "synthetic_fixture": False},
            retry_delay_seconds=0,
        )
    )

    assert report.status is SuiteStatus.blocked
    assert any(item.startswith("BLOCKED_GOLD_MAPPING:") for item in report.blockers)
    assert lookup.generation_ids == [projection.generation.id]
    assert not report.query_records


def test_formal_entry_rejects_semantic_sidecar_without_hash_before_projection_load() -> None:
    dataset, protocol, projection = _semantic_run_inputs()
    dataset = replace(
        dataset,
        manifest=dataset.manifest.model_copy(
            update={"relation_expectations_sha256": None}
        ),
    )
    schedule = _schedule(dataset, protocol, projection)
    lookup = _ProjectionLookup(projection)

    report = _drive_immediate(
        run_formal_matrix(
            schedule,
            protocol,
            dataset,
            _NoCallExecutor(),
            generation_resolver=_GenerationStore(projection.generation),
            gold_projection_lookup=lookup,
            policy_training_summary={"formal_ready": True, "synthetic_fixture": False},
            retry_delay_seconds=0,
        )
    )

    assert report.status is SuiteStatus.blocked
    assert any(item.startswith("BLOCKED_GOLD_MAPPING:") for item in report.blockers)
    assert lookup.generation_ids == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_manifest_sha256", None),
        ("dataset_content_sha256", "f" * 64),
        ("relation_expectations_sha256", "f" * 64),
        ("required_relation_ids_sha256", "e" * 64),
        ("required_relation_queue_sha256", "d" * 64),
        ("dataset_id", "replaced-dataset"),
    ],
)
def test_formal_entry_rejects_old_or_replaced_schedule_binding_before_projection_load(
    field: str, value: object
) -> None:
    dataset, protocol, projection = _semantic_run_inputs()
    schedule = replace(_schedule(dataset, protocol, projection), **{field: value})
    resolver = _PersistedStore(
        projection.generation,
        _database_for_projection(projection),
    )
    lookup = _ProjectionLookup(projection)

    report = _drive_immediate(
        run_formal_matrix(
            schedule,
            protocol,
            dataset,
            _NoCallExecutor(),
            generation_resolver=resolver,
            gold_projection_lookup=lookup,
            policy_training_summary={"formal_ready": True, "synthetic_fixture": False},
            retry_delay_seconds=0,
        )
    )

    assert report.status is SuiteStatus.blocked
    assert any(item.startswith("BLOCKED_GOLD_MAPPING:") for item in report.blockers)
    assert resolver.calls == 0
    assert lookup.generation_ids == []
    assert not report.query_records


def test_loaded_expectation_mapping_cannot_be_replaced_after_hash_verification() -> None:
    dataset, _protocol, _projection = _semantic_run_inputs()

    with pytest.raises(TypeError):
        dataset.relation_expectations[DATA_RELATION_ID] = dataset.relation_expectations[
            DATA_RELATION_ID
        ]


def test_formal_entry_blocks_mutated_loaded_expectation_content_before_provider() -> None:
    dataset, protocol, projection = _semantic_run_inputs()
    schedule = _schedule(dataset, protocol, projection)
    dataset.relation_expectations[DATA_RELATION_ID].qualifier_values["replaced"] = True
    resolver = _PersistedStore(
        projection.generation,
        _database_for_projection(projection),
    )

    report = _drive_immediate(
        run_formal_matrix(
            schedule,
            protocol,
            dataset,
            _NoCallExecutor(),
            generation_resolver=resolver,
            policy_training_summary={"formal_ready": True, "synthetic_fixture": False},
            retry_delay_seconds=0,
        )
    )

    assert report.status is SuiteStatus.blocked
    assert any("loader-verified dataset content was replaced" in item for item in report.blockers)
    assert resolver.calls == 0
    assert not report.query_records


@pytest.mark.parametrize(
    ("result_index", "column", "mode"),
    [
        (11, "id", "payload"),
        (11, "document_id", "row"),
        (11, "parent_id", "row"),
        (11, "is_leaf", "row"),
        (11, "text", "row"),
        (11, "text_hash", "row"),
        (12, "id", "payload"),
        (12, "chunk_id", "row"),
        (12, "provenance_id", "row"),
        (12, "start", "row"),
        (12, "end", "row"),
        (12, "quote", "row"),
    ],
)
def test_formal_entry_rejects_chunk_or_evidence_column_payload_drift_before_provider(
    result_index: int, column: str, mode: str
) -> None:
    dataset, protocol, projection = _semantic_run_inputs()
    schedule = _schedule(dataset, protocol, projection)
    database = _database_for_projection(projection)
    row = database.value.results[result_index][0]
    if mode == "payload":
        row.payload[column] = "00000000-0000-4000-8000-000000000099"
    elif column == "parent_id":
        row.parent_id = "00000000-0000-4000-8000-000000000099"
    elif column in {"document_id", "chunk_id", "provenance_id"}:
        setattr(row, column, "00000000-0000-4000-8000-000000000099")
    elif column == "is_leaf":
        row.is_leaf = not row.is_leaf
    elif column == "text":
        row.text += "!"
    elif column == "text_hash":
        row.text_hash = "f" * 64
    elif column in {"start", "end"}:
        setattr(row, column, getattr(row, column) + 1)
    elif column == "quote":
        row.quote += "!"
    else:
        raise AssertionError(f"unhandled duplicate column: {column}")
    store = _PersistedStore(projection.generation, database)

    report = _drive_immediate(
        run_formal_matrix(
            schedule,
            protocol,
            dataset,
            _NoCallExecutor(),
            generation_resolver=store,
            policy_training_summary={"formal_ready": True, "synthetic_fixture": False},
            retry_delay_seconds=0,
        )
    )

    assert report.status is SuiteStatus.blocked
    assert any(item.startswith("BLOCKED_GOLD_MAPPING:") for item in report.blockers)
    assert not any(item.startswith("BLOCKED_PROVIDER:") for item in report.blockers)
    assert not report.query_records
