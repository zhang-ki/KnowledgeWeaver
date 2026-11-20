from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import (
    FakeEntityIndex,
    FakeGraphStore,
    FakeKnowledgeStore,
)
from pydantic import ValidationError

from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError
from knowledge_weaver.core.ids import deterministic_id
from knowledge_weaver.graph import GraphRetriever
from knowledge_weaver.models import (
    GraphOptions,
    IndexCandidate,
    ProvenanceMethod,
    RetrievalQuery,
)


def _retriever(case: dict[str, Any], graph_store: FakeGraphStore | None = None) -> GraphRetriever:
    return GraphRetriever(
        graph_store
        or FakeGraphStore(lambda context: case["snapshot"].model_copy(update={"context": context})),
        FakeEntityIndex(),
        FakeKnowledgeStore(case["evidence"], case["chunks"]),
    )


def _query(
    context: Any,
    seed: str | None = None,
    *,
    top_k: int = 10,
    options: GraphOptions | None = None,
) -> RetrievalQuery:
    return RetrievalQuery(
        text="Pipeline evidence",
        context=context,
        top_k=top_k,
        entity_ids=[seed] if seed else [],
        graph_options=options,
    )


@pytest.mark.asyncio
async def test_two_hop_paths_map_each_step_to_checkable_original_chunk(
    case_factory: Any, context_factory: Any
) -> None:
    context = context_factory()
    case = case_factory(context)
    retriever = _retriever(case)
    result = await retriever.retrieve(
        _query(context, case["entities"]["Pipeline"]["id"])
    )

    two_hop = next(path for path in result.paths if len(path.steps) == 2)
    assert two_hop.entity_ids == [
        case["entities"]["Pipeline"]["id"],
        case["entities"]["Evidence"]["id"],
        case["entities"]["Repository"]["id"],
    ]
    for step in two_hop.steps:
        assert step.evidence_ids
        for evidence_id in step.evidence_ids:
            evidence = case["evidence"][evidence_id]
            chunk = case["chunks"][evidence.chunk_id]
            evidence.assert_matches_chunk(chunk)
            assert evidence.quote in chunk.text

    hits_by_chunk = {hit.chunk_id: hit for hit in result.hits}
    evidence_ab = case["evidence"][deterministic_id("b05_evidence", "ab")]
    evidence_bc = case["evidence"][deterministic_id("b05_evidence", "bc")]
    assert hits_by_chunk[evidence_ab.chunk_id].score == pytest.approx(1 / 2)
    assert hits_by_chunk[evidence_bc.chunk_id].score == pytest.approx(1 / 3)
    assert two_hop.id in hits_by_chunk[evidence_bc.chunk_id].path_ids


@pytest.mark.asyncio
async def test_default_two_hop_limit_and_hard_three_hop_limit(
    case_factory: Any, context_factory: Any
) -> None:
    context = context_factory()
    case = case_factory(context)
    retriever = _retriever(case)
    seed = case["entities"]["Pipeline"]["id"]

    default_result = await retriever.retrieve(_query(context, seed))
    assert max(len(path.steps) for path in default_result.paths) == 2
    assert all(len(path.steps) <= 2 for path in default_result.paths)

    three_hop = await retriever.retrieve(
        _query(context, seed, options=GraphOptions(max_hops=3))
    )
    assert any(
        len(path.steps) == 3
        and path.entity_ids[-1] == case["entities"]["Graph"]["id"]
        for path in three_hop.paths
    )
    with pytest.raises(ValidationError):
        GraphOptions(max_hops=4)
    with pytest.raises(ValidationError):
        GraphOptions(max_edges=501)


@pytest.mark.asyncio
async def test_reverse_traversal_is_recorded_without_rewriting_relation_direction(
    case_factory: Any, context_factory: Any
) -> None:
    context = context_factory()
    case = case_factory(context)
    result = await _retriever(case).retrieve(
        _query(context, case["entities"]["Repository"]["id"], options=GraphOptions(max_hops=1))
    )

    reverse_step = next(
        step
        for path in result.paths
        for step in path.steps
        if step.relation_id == deterministic_id("b05_relation", "bc")
    )
    assert reverse_step.traversed_reverse is True
    assert reverse_step.from_id == case["entities"]["Repository"]["id"]
    assert reverse_step.to_id == case["entities"]["Evidence"]["id"]


@pytest.mark.asyncio
async def test_out_direction_does_not_traverse_an_edge_backwards(
    case_factory: Any, context_factory: Any
) -> None:
    context = context_factory()
    case = case_factory(context)
    result = await _retriever(case).retrieve(
        _query(
            context,
            case["entities"]["Repository"]["id"],
            options=GraphOptions(max_hops=1, direction="out"),
        )
    )

    relation_ids = {step.relation_id for path in result.paths for step in path.steps}
    assert relation_ids == {deterministic_id("b05_relation", "cd")}
    assert all(not step.traversed_reverse for path in result.paths for step in path.steps)


@pytest.mark.asyncio
async def test_parallel_positive_and_negative_relations_are_both_retained(
    case_factory: Any, context_factory: Any
) -> None:
    context = context_factory()
    case = case_factory(context, include_conflict=True)
    result = await _retriever(case).retrieve(
        _query(context, case["entities"]["Pipeline"]["id"], options=GraphOptions(max_hops=1))
    )

    direct_ids = {path.steps[0].relation_id for path in result.paths if len(path.steps) == 1}
    assert deterministic_id("b05_relation", "ab") in direct_ids
    assert deterministic_id("b05_relation", "ab-negative") in direct_ids
    assert any("positive/negative relation conflict" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_same_chunk_hit_keeps_the_maximum_score_across_paths(
    case_factory: Any, context_factory: Any
) -> None:
    context = context_factory()
    case = case_factory(context, alternate_route=True)
    result = await _retriever(case).retrieve(
        _query(context, case["entities"]["Pipeline"]["id"], options=GraphOptions(max_hops=2))
    )

    evidence_ab = case["evidence"][deterministic_id("b05_evidence", "ab")]
    hit = next(item for item in result.hits if item.chunk_id == evidence_ab.chunk_id)
    assert hit.score == pytest.approx(1 / 2)
    assert len(hit.path_ids) >= 2


@pytest.mark.asyncio
async def test_cycles_are_not_revisited_and_edge_budget_marks_partial(
    case_factory: Any, context_factory: Any
) -> None:
    context = context_factory()
    cyclic_case = case_factory(context, include_cycle=True)
    cyclic_result = await _retriever(cyclic_case).retrieve(
        _query(
            context,
            cyclic_case["entities"]["Pipeline"]["id"],
            options=GraphOptions(max_hops=3),
        )
    )
    assert all(len(path.entity_ids) == len(set(path.entity_ids)) for path in cyclic_result.paths)

    fanout_case = case_factory(context, fan_out=8)
    bounded = await _retriever(fanout_case).retrieve(
        _query(
            context,
            fanout_case["entities"]["Pipeline"]["id"],
            top_k=10,
            options=GraphOptions(max_hops=3, max_edges=3),
        )
    )
    assert bounded.partial is True
    assert any("edge budget exhausted" in warning for warning in bounded.warnings)
    assert len(bounded.paths) <= 3


@pytest.mark.asyncio
async def test_missing_evidence_relation_is_rejected_and_reported(
    case_factory: Any, context_factory: Any
) -> None:
    context = context_factory()
    case = case_factory(context)
    invalid = case["relations"][0].model_copy(
        update={"evidence_ids": [deterministic_id("b05_evidence", "absent")]}
    )
    bad_snapshot = case["snapshot"].model_copy(update={"relations": [invalid]})
    graph_store = FakeGraphStore(lambda _: bad_snapshot)
    result = await _retriever(case, graph_store).retrieve(
        _query(context, case["entities"]["Pipeline"]["id"])
    )

    assert result.paths == []
    assert result.hits == []
    assert any("incomplete references" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_hydrated_evidence_must_match_snapshot_and_original_chunk(
    case_factory: Any, context_factory: Any
) -> None:
    context = context_factory()
    case = case_factory(context)
    corrupted_evidence = dict(case["evidence"])
    evidence_id = deterministic_id("b05_evidence", "ab")
    corrupted_evidence[evidence_id] = corrupted_evidence[evidence_id].model_copy(
        update={"quote": "x" * len(corrupted_evidence[evidence_id].quote)}
    )
    retriever = GraphRetriever(
        FakeGraphStore(lambda _: case["snapshot"]),
        FakeEntityIndex(),
        FakeKnowledgeStore(corrupted_evidence, case["chunks"]),
    )

    with pytest.raises(KnowledgeWeaverError) as error:
        await retriever.retrieve(
            _query(context, case["entities"]["Pipeline"]["id"], options=GraphOptions(max_hops=1))
        )
    assert error.value.code == ErrorCode.SCHEMA_INVALID


@pytest.mark.asyncio
async def test_cross_source_relation_without_direct_evidence_is_rejected(
    case_factory: Any, context_factory: Any
) -> None:
    context = context_factory(include_inferred=True)
    case = case_factory(context, evidence_method_overrides={"ab": ProvenanceMethod.RULE_INFERRED})
    result = await _retriever(case).retrieve(
        _query(context, case["entities"]["Pipeline"]["id"], options=GraphOptions(max_hops=1))
    )

    assert all(
        step.relation_id != deterministic_id("b05_relation", "ab")
        for path in result.paths
        for step in path.steps
    )
    assert any("unsupported cross-source evidence" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_inferred_relations_are_excluded_by_default_and_opt_in_is_explicit(
    case_factory: Any, context_factory: Any
) -> None:
    context = context_factory()
    case = case_factory(context, relation_method_overrides={"bc": ProvenanceMethod.RULE_INFERRED})
    seed = case["entities"]["Pipeline"]["id"]
    default_result = await _retriever(case).retrieve(_query(context, seed))
    assert all(
        step.relation_id != deterministic_id("b05_relation", "bc")
        for path in default_result.paths
        for step in path.steps
    )

    opted_context = context_factory(include_inferred=True)
    opted_case = case_factory(
        opted_context,
        relation_method_overrides={"bc": ProvenanceMethod.RULE_INFERRED},
    )
    opted_result = await _retriever(opted_case).retrieve(
        _query(opted_context, opted_case["entities"]["Pipeline"]["id"])
    )
    assert any(
        step.relation_id == deterministic_id("b05_relation", "bc")
        for path in opted_result.paths
        for step in path.steps
    )


@pytest.mark.asyncio
async def test_zero_index_seeds_return_empty_without_scanning_graph(
    case_factory: Any, context_factory: Any
) -> None:
    context = context_factory()
    case = case_factory(context)
    graph_store = FakeGraphStore(lambda _: case["snapshot"])
    entity_index = FakeEntityIndex()
    retriever = GraphRetriever(
        graph_store,
        entity_index,
        FakeKnowledgeStore(case["evidence"], case["chunks"]),
    )

    result = await retriever.retrieve(_query(context))
    assert result.hits == []
    assert result.paths == []
    assert result.partial is False
    assert graph_store.calls == []
    assert len(entity_index.queries) == 1
    assert entity_index.queries[0].context.generation_id == context.generation_id


@pytest.mark.asyncio
async def test_entity_index_candidate_chunk_resolves_only_mentioned_entities(
    case_factory: Any, context_factory: Any
) -> None:
    context = context_factory()
    case = case_factory(context)
    candidate_chunk = case["evidence"][deterministic_id("b05_evidence", "ab")].chunk_id
    index = FakeEntityIndex(
        [
            IndexCandidate(
                chunk_id=candidate_chunk,
                rank=1,
                raw_score=1.0,
                score_direction="higher",
            )
        ]
    )
    retriever = GraphRetriever(
        FakeGraphStore(lambda _: case["snapshot"]),
        index,
        FakeKnowledgeStore(case["evidence"], case["chunks"]),
    )

    result = await retriever.retrieve(_query(context, top_k=10))
    assert result.paths
    assert index.queries[0].channel.value == "entity"
    assert all(
        path.entity_ids[0]
        in {case["entities"]["Pipeline"]["id"], case["entities"]["Evidence"]["id"]}
        for path in result.paths
    )


@pytest.mark.asyncio
async def test_snapshot_cache_isolated_by_source_filter_and_generation(
    case_factory: Any, context_factory: Any
) -> None:
    first_context = context_factory(source_ids=[deterministic_id("b05_source", "manual")])
    case = case_factory(first_context)
    graph_store = FakeGraphStore(lambda context: case_factory(context)["snapshot"])
    retriever = GraphRetriever(
        graph_store,
        FakeEntityIndex(),
        FakeKnowledgeStore(case["evidence"], case["chunks"]),
    )
    seed = case["entities"]["Evidence"]["id"]

    first_result = await retriever.retrieve(_query(first_context, seed))
    same_scope = context_factory(
        generation_id=first_context.generation_id,
        source_ids=first_context.source_ids,
    )
    await retriever.retrieve(_query(same_scope, seed))
    second_context = context_factory(
        generation_id=first_context.generation_id,
        source_ids=[deterministic_id("b05_source", "repository")],
    )
    second_result = await retriever.retrieve(_query(second_context, seed))
    third_context = context_factory(
        generation_id=deterministic_id("b05_generation", "two"),
        source_ids=second_context.source_ids,
    )
    await retriever.retrieve(_query(third_context, seed))

    assert len(graph_store.calls) == 3
    first_relation_ids = {step.relation_id for path in first_result.paths for step in path.steps}
    second_relation_ids = {step.relation_id for path in second_result.paths for step in path.steps}
    assert deterministic_id("b05_relation", "bc") not in first_relation_ids
    assert deterministic_id("b05_relation", "ab") not in second_relation_ids
    assert deterministic_id("b05_relation", "bc") in second_relation_ids


@pytest.mark.asyncio
async def test_top_k_never_returns_a_path_without_all_step_chunk_hits(
    case_factory: Any, context_factory: Any
) -> None:
    context = context_factory()
    case = case_factory(context)
    result = await _retriever(case).retrieve(
        _query(context, case["entities"]["Pipeline"]["id"], top_k=1)
    )

    assert len(result.hits) <= 1
    assert all(
        step.evidence_ids
        and all(
            case["evidence"][evidence_id].chunk_id
            in {hit.chunk_id for hit in result.hits}
            for evidence_id in step.evidence_ids
        )
        for path in result.paths
        for step in path.steps
    )


@pytest.mark.asyncio
async def test_missing_seed_and_expired_deadline_are_explicit_errors(
    case_factory: Any, context_factory: Any
) -> None:
    context = context_factory()
    case = case_factory(context)
    retriever = _retriever(case)
    with pytest.raises(KnowledgeWeaverError) as missing:
        await retriever.retrieve(
            _query(context, deterministic_id("b05_entity", "missing"))
        )
    assert missing.value.code == ErrorCode.MISSING_REFERENCE

    expired_context = context.model_copy(
        update={"deadline_utc": datetime.now(UTC) - timedelta(seconds=1)}
    )
    with pytest.raises(KnowledgeWeaverError) as deadline:
        await retriever.retrieve(_query(expired_context, case["entities"]["Pipeline"]["id"]))
    assert deadline.value.code == ErrorCode.DEADLINE_EXCEEDED
