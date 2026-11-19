from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from knowledge_weaver.core.ids import document_id
from knowledge_weaver.models import (
    Block,
    Chunk,
    Document,
    EmbeddingBatch,
    Entity,
    Evidence,
    GraphSnapshot,
    Source,
    SourceKind,
    SourceVersion,
)
from knowledge_weaver.models import (
    Path as GraphPath,
)
from knowledge_weaver.models.schema import MODEL_TYPES, json_schema_registry
from knowledge_weaver.ports import __all__ as port_names

FIXTURES = Path(__file__).parents[1] / "fixtures" / "contracts"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_four_source_fixture_round_trips_with_typed_locators() -> None:
    payload = fixture("four_source_structures.json")
    sources = [Source.model_validate(item) for item in payload["sources"]]
    documents = [Document.model_validate(item) for item in payload["documents"]]
    source_versions = [SourceVersion.model_validate(item) for item in payload["source_versions"]]

    assert {item.kind for item in sources} == set(SourceKind)
    assert {item.blocks[0].locator.kind for item in documents} == {"python", "pdf", "docx", "markdown"}
    assert len(source_versions) == 4
    for raw_block in [block for doc in payload["documents"] for block in doc["blocks"]]:
        parsed = Block.model_validate(raw_block)
        canonical = Block.model_validate(parsed.model_dump(mode="json"))
        assert canonical == parsed


def test_graph_fixture_is_closed_and_two_hops() -> None:
    payload = fixture("closed_two_hop_graph.json")
    graph = GraphSnapshot.model_validate({key: value for key, value in payload.items() if key != "path"})
    path = GraphPath.model_validate(payload["path"])

    assert len(path.steps) == 2
    assert len(path.entity_ids) == 3
    assert path.steps[0].to_id == path.steps[1].from_id
    assert all(set(step.evidence_ids).issubset({item.id for item in graph.evidence}) for step in path.steps)
    assert GraphSnapshot.model_validate(graph.model_dump(mode="json")) == graph


def test_pseudo_evidence_counterexample_is_rejected_against_chunk() -> None:
    payload = fixture("pseudo_evidence_counterexample.json")
    chunk = Chunk.model_validate(payload["chunk"])
    evidence = Evidence.model_validate(payload["evidence"])

    with pytest.raises(ValueError, match="quote does not match chunk text"):
        evidence.assert_matches_chunk(chunk)


def test_fixed_document_id_and_schema_registry() -> None:
    assert document_id(
        "00000000-0000-4000-8000-000000000001",
        "src/example.py",
        "a" * 64,
        "fixture-parser-v1",
    ) == "1fd1f6cd-e83d-5ad1-818e-a40f63bca628"
    registry = json_schema_registry()
    assert set(registry) == {model.__name__ for model in MODEL_TYPES}
    assert {"Document", "GraphSnapshot", "RunSpec", "ErrorEnvelope"}.issubset(registry)


def test_models_reject_extra_fields_and_invalid_embeddings() -> None:
    with pytest.raises(ValidationError):
        Entity.model_validate({"id": "00000000-0000-4000-8000-000000000001", "extra": True})
    with pytest.raises(ValidationError, match="unit L2 norm"):
        EmbeddingBatch(
            vectors=[[1.0, 1.0]], dimension=2, normalized=True, model="m", revision="r",
            fingerprint="f", usage={"count_kind": "unavailable"},
        )


def test_ports_export_the_frozen_interface_set() -> None:
    expected = {
        "Parser", "Chunker", "Extractor", "PipelineService", "KnowledgeStore",
        "CandidateIndex", "GraphStore", "Retriever", "HybridRetriever", "Reranker",
        "EvidenceVerifier", "RetrievalPolicy", "AgentService", "GenerationProvider",
        "EmbeddingProvider", "EvaluationRunner",
    }
    assert set(port_names) == expected
