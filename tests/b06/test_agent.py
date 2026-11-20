from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    FakeRetriever,
    FakeStore,
    FakeVerifier,
    RecordingProvider,
    make_budget,
    make_chunk_evidence,
    make_context,
    make_query,
)

from knowledge_weaver.agent import (
    FEATURE_ORDER,
    AgentServiceImpl,
    EvidenceLedger,
    RulePolicy,
    SegrPolicy,
    SegrPolicyWeights,
    load_policy_weights,
)
from knowledge_weaver.agent.features import extract_exact_symbol, feature_vector
from knowledge_weaver.agent.service import _build_candidates, _safe_features
from knowledge_weaver.agent.verifier import StructuredEvidenceVerifier
from knowledge_weaver.agent.weights import (
    ActionCostMedian,
    feature_schema_fingerprint,
    policy_fingerprint,
)
from knowledge_weaver.core.ids import deterministic_id
from knowledge_weaver.models import (
    Action,
    ActionTool,
    AnswerStatus,
    CountKind,
    IndexChannel,
    InvestigationRequest,
    InvestigationState,
    Observation,
    PolicyKind,
    QueryContext,
    RetrievalHit,
    RetrievalQuery,
    RetrievalResult,
    SourceKind,
    StopReason,
    Usage,
    VerifyRequest,
)


def _state(
    context: QueryContext,
    text: str = "What does `build_index` do?",
    *,
    required: list[SourceKind] | None = None,
    memory: dict[str, Any] | None = None,
) -> InvestigationState:
    query = make_query(context, text, required_source_types=required)
    budget = make_budget(context, max_total_tokens=1_000)
    return InvestigationState(
        query=query,
        subquestions=query.subquestions,
        evidence_ledger=[],
        unresolved=[query.subquestions[0].id],
        conflicts=[],
        paths=[],
        history=[],
        remaining_budget=budget,
        candidates=[],
        policy_memory={
            "remaining_budget_ms": 10_000,
            "initial_budget_ms": 10_000,
            "remaining_budget_ratio": 1.0,
            "initial_max_steps": 6,
            "family_counts": {},
            **(memory or {}),
        },
    )


def _action(
    context: QueryContext,
    family: ActionTool,
    *,
    facet_id: str | None = None,
    top_k: int = 10,
    tokens: int = 100,
    ms: int = 1_000,
) -> Action:
    facet = make_query(context, "What does `build_index` do?").subquestions[0]
    return Action(
        id=deterministic_id("b06_test_action", {"family": family.value, "top_k": top_k}),
        tool=family,
        facet_id=facet_id or facet.id,
        query="What does `build_index` do?",
        top_k=top_k,
        estimated_tokens=tokens,
        estimated_ms=ms,
    )


def _weights(*, coefficient: float = 0.0) -> SegrPolicyWeights:
    values: dict[str, Any] = {
        "schema_version": 1,
        "algorithm": "segr_ridge_v1",
        "policy_mode": "learned",
        "feature_order": FEATURE_ORDER,
        "action_coefficients": {"bm25": [coefficient] * 12},
        "action_sample_counts": {"bm25": 24},
        "prior_action_families": [],
        "cost_medians": {"bm25": ActionCostMedian(estimated_tokens=10, elapsed_ms=100)},
        "ridge_lambda": 1.0,
        "lambda_cost": 0.3,
        "stop_threshold": 0.05,
        "training_split": "train",
        "selection_split": "dev",
        "train_dataset_sha256": "a" * 64,
        "train_manifest_sha256": "b" * 64,
        "train_action_log_sha256": "c" * 64,
        "feature_schema_sha256": feature_schema_fingerprint(),
        "policy_fingerprint": "0" * 64,
    }
    provisional = SegrPolicyWeights.model_construct(**values)
    values["policy_fingerprint"] = policy_fingerprint(provisional)
    return SegrPolicyWeights.model_validate(values)


def _request(context: QueryContext, *, policy: PolicyKind = PolicyKind.fixed, features: dict[str, Any] | None = None, budget: Any | None = None) -> InvestigationRequest:
    query = make_query(context)
    return InvestigationRequest(
        query=query,
        context=context,
        budget=budget or make_budget(context),
        policy=policy,
        features=features or {},
    )


def _result_for(chunk: Any, evidence: Any, context: QueryContext) -> RetrievalResult:
    return RetrievalResult(
        context=context,
        hits=[
            RetrievalHit(
                chunk_id=chunk.id,
                evidence_ids=[evidence.id],
                score=1.0,
                channel="bm25",
                rank=1,
            )
        ],
        usage=Usage(tool_calls=1, count_kind=CountKind.exact),
    )


def _fake_registry(context: QueryContext, result: RetrievalResult) -> dict[str, FakeRetriever]:
    return {
        family: FakeRetriever(lambda query: result.model_copy(update={"context": query.context}))
        for family in ("bm25", "dense", "symbol", "entity", "graph")
    }


def test_12d_features_and_query_ema_cost_are_hand_calculable() -> None:
    context = make_context()
    state = _state(context, required=[SourceKind.python_repo])
    action = _action(context, ActionTool.symbol)

    vector = feature_vector(state, action)
    assert len(vector) == 12
    assert vector[0:6] == pytest.approx([1.0, 1.0, 1.0, 0.0, 0.0, 1.0])
    assert vector[6] == pytest.approx(0.5)  # EMA=0 maps to the frozen [0, 1] feature range.
    assert vector[7] == pytest.approx(0.2)  # Four unique lexical tokens / 20.
    assert vector[8:12] == pytest.approx([1.0, 0.0, 0.0, 1.0])
    assert extract_exact_symbol("What is the repository structure?") is None
    assert extract_exact_symbol("What does build_index do?") == "build_index"

    policy = SegrPolicy()
    observed = policy.observe(
        state,
        Observation(action=action, reward=0.6, usage=Usage(count_kind=CountKind.exact)),
    )
    assert observed.policy_memory["gain_ema"]["symbol"] == pytest.approx(0.3)
    decision = policy.choose(observed, [action])
    score = decision.candidate_scores[0]
    assert score.predicted_gain == pytest.approx(0.54)  # 0.8 * 0.6 prior + 0.2 * 0.3 EMA.
    assert score.estimated_cost == pytest.approx(0.1)  # 0.5*100/1000 + 0.5*1000/10000.
    assert score.utility == pytest.approx(0.51)  # gain - 0.3*cost; no duplicate penalty.
    assert "profile=prior" in (score.reason or "")


def test_learned_tie_is_resolved_by_stable_action_key() -> None:
    context = make_context()
    state = _state(context)
    low = _action(context, ActionTool.bm25, top_k=5, tokens=50, ms=100)
    high = _action(context, ActionTool.bm25, top_k=10, tokens=50, ms=100)
    decision = SegrPolicy(_weights()).choose(state, [high, low])

    assert decision.action == low
    assert all("profile=learned" in (item.reason or "") for item in decision.candidate_scores)


def test_rule_policy_routes_symbol_and_multihop_gaps_differently() -> None:
    context = make_context()
    symbol_state = _state(context, "Show the exact implementation of `build_index`.", required=[SourceKind.python_repo])
    symbol_actions = [
        _action(context, ActionTool.symbol, facet_id=symbol_state.subquestions[0].id),
        _action(context, ActionTool.graph, facet_id=symbol_state.subquestions[0].id),
    ]
    assert RulePolicy().choose(symbol_state, symbol_actions).action.tool is ActionTool.symbol

    graph_state = _state(context, "How does Pipeline connect to Evidence through Repository?", required=[SourceKind.markdown, SourceKind.python_repo])
    graph_actions = [
        _action(context, ActionTool.symbol, facet_id=graph_state.subquestions[0].id),
        _action(context, ActionTool.graph, facet_id=graph_state.subquestions[0].id),
    ]
    assert RulePolicy().choose(graph_state, graph_actions).action.tool is ActionTool.graph


def test_policy_loader_validates_b07_artifact_fingerprint(tmp_path: Path) -> None:
    weights = _weights(coefficient=0.25)
    path = tmp_path / "segr.json"
    path.write_text(weights.model_dump_json(), encoding="utf-8")

    assert load_policy_weights(path) == weights
    value = weights.model_dump(mode="json")
    value["policy_fingerprint"] = "f" * 64
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint"):
        load_policy_weights(path)


@pytest.mark.asyncio
async def test_evidence_ledger_keeps_citations_but_deduplicates_duplicate_group_coverage() -> None:
    context = make_context()
    duplicate_group = deterministic_id("b06_duplicate_group", "same source text")
    chunk_a, evidence_a = make_chunk_evidence(text="The graph stores source evidence.", duplicate_group=duplicate_group)
    chunk_b, evidence_b = make_chunk_evidence(text="The graph stores source evidence copied.", duplicate_group=duplicate_group)
    store = FakeStore(
        {chunk_a.id: chunk_a, chunk_b.id: chunk_b},
        {evidence_a.id: evidence_a, evidence_b.id: evidence_b},
    )
    ledger = EvidenceLedger()
    for chunk, evidence in ((chunk_a, evidence_a), (chunk_b, evidence_b)):
        result = _result_for(chunk, evidence, context)
        new_ids = await ledger.absorb(result, store, context)
        assert new_ids == [evidence.id]

    assert len(ledger.entries) == 2  # both original citations remain available
    assert {entry.duplicate_key for entry in ledger.entries.values()} == {duplicate_group}


@pytest.mark.asyncio
async def test_evidence_ledger_maps_python_locator_to_source_family() -> None:
    context = make_context()
    chunk, evidence = make_chunk_evidence(kind="python")
    store = FakeStore({chunk.id: chunk}, {evidence.id: evidence})
    ledger = EvidenceLedger()

    await ledger.absorb(_result_for(chunk, evidence, context), store, context)

    assert ledger.entries[evidence.id].source_kind == SourceKind.python_repo.value


@pytest.mark.asyncio
async def test_real_b04_channels_and_b05_graph_retriever_are_assembled(
    evidence_bundle: Any,
    real_b04_retrievers: Any,
) -> None:
    from knowledge_weaver.graph import GraphRetriever
    from knowledge_weaver.retrieval import (
        Bm25Retriever,
        DenseRetriever,
        EntityRetriever,
        SymbolRetriever,
    )

    context = make_context()
    store, _chunk, evidence = evidence_bundle
    tools, index, graph_index = real_b04_retrievers
    assert isinstance(tools["bm25"], Bm25Retriever)
    assert isinstance(tools["dense"], DenseRetriever)
    assert isinstance(tools["symbol"], SymbolRetriever)
    assert isinstance(tools["entity"], EntityRetriever)
    assert isinstance(tools["graph"], GraphRetriever)
    provider = RecordingProvider()
    service = AgentServiceImpl(
        store,
        tools,
        provider,
        verifier_factory=lambda generate: FakeVerifier(),
    )
    answer = await service.investigate(_request(context))

    assert answer.status is AnswerStatus.answered
    assert answer.claims[0].evidence_ids == [evidence.id]
    assert index.queries and index.queries[0].channel is IndexChannel.bm25
    assert not graph_index.queries
    assert store.validation_calls == 1
    assert answer.usage.tool_calls == 2  # one B04 action plus stored-citation verification
    assert service.get_trace(answer.investigation_id)[-1]["stop_reason"] == StopReason.sufficient_evidence.value


@pytest.mark.asyncio
async def test_b05_graph_action_uses_entity_index_seed_port(real_b04_retrievers: Any, evidence_bundle: Any) -> None:
    context = make_context()
    store, _, _ = evidence_bundle
    tools, _, graph_index = real_b04_retrievers
    service = AgentServiceImpl(
        store,
        tools,
        RecordingProvider(),
        verifier_factory=lambda generate: FakeVerifier(),
    )
    query = make_query(
        context,
        "How does Pipeline connect to Evidence through Repository?",
        required_source_types=[SourceKind.markdown, SourceKind.python_repo],
    )
    request = InvestigationRequest(
        query=query,
        context=context,
        budget=make_budget(context, max_steps=1),
        policy=PolicyKind.rule,
    )

    answer = await service.investigate(request)

    assert answer.status is AnswerStatus.insufficient
    assert len(graph_index.queries) == 1
    assert graph_index.queries[0].channel is IndexChannel.entity


@pytest.mark.asyncio
async def test_insufficient_answer_without_evidence_and_cancelled_request_stop_safely() -> None:
    context = make_context()
    empty = RetrievalResult(context=context, hits=[], usage=Usage(count_kind=CountKind.exact))
    retrievers = _fake_registry(context, empty)
    provider = RecordingProvider()
    service = AgentServiceImpl(FakeStore({}, {}), retrievers, provider, verifier_factory=lambda generate: FakeVerifier())

    no_evidence = await service.investigate(
        _request(context, budget=make_budget(context, max_steps=1))
    )
    assert no_evidence.status is AnswerStatus.insufficient
    assert no_evidence.claims == []
    assert no_evidence.usage.tool_calls == 1
    assert provider.requests == []  # no evidence means no answer-generation call

    cancelled = await service.investigate(_request(context, features={"cancelled": True}))
    assert cancelled.status is AnswerStatus.insufficient
    assert cancelled.usage.tool_calls == 0
    assert service.get_trace(cancelled.investigation_id)[-1]["stop_reason"] == StopReason.cancelled.value


@pytest.mark.asyncio
async def test_llm_router_and_answer_share_one_budgeted_generation_provider(evidence_bundle: Any) -> None:
    context = make_context()
    store, chunk, evidence = evidence_bundle
    result = _result_for(chunk, evidence, context)
    provider = RecordingProvider()
    service = AgentServiceImpl(
        store,
        _fake_registry(context, result),
        provider,
        verifier_factory=lambda generate: FakeVerifier(),
    )

    answer = await service.investigate(_request(context, policy=PolicyKind.llm))

    assert answer.status is AnswerStatus.answered
    assert len(provider.requests) == 2
    assert "Choose exactly one offered action_id" in provider.requests[0].system_instruction
    assert "Answer only from the supplied evidence" in provider.requests[1].system_instruction
    assert answer.usage.llm_calls == 2
    assert "policy_profile" in service.get_trace(answer.investigation_id)[0]


@pytest.mark.asyncio
async def test_unknown_dense_usage_is_conservatively_charged(evidence_bundle: Any) -> None:
    context = make_context()
    store, chunk, evidence = evidence_bundle
    unknown = RetrievalResult(
        context=context,
        hits=[RetrievalHit(chunk_id=chunk.id, evidence_ids=[evidence.id], score=1.0, channel="dense", rank=1)],
        usage=Usage(count_kind=CountKind.unavailable),
    )
    def fail_bm25(query: RetrievalQuery) -> RetrievalResult:
        raise RuntimeError("synthetic BM25 fault")

    retrievers = _fake_registry(context, unknown)
    retrievers["bm25"] = FakeRetriever(fail_bm25)
    provider = RecordingProvider()
    service = AgentServiceImpl(
        store,
        retrievers,
        provider,
        verifier_factory=lambda generate: FakeVerifier(),
    )
    request = _request(context, policy=PolicyKind.rule, budget=make_budget(context, max_steps=2))
    request = request.model_copy(update={"query": make_query(context, "Describe the retrieval result.")})
    answer = await service.investigate(request)

    assert answer.usage.tool_calls == 3  # failed BM25, dense retrieval, citation validation
    assert answer.usage.input_tokens >= 128
    assert answer.usage.count_kind is CountKind.estimated
    assert answer.status is AnswerStatus.answered


@pytest.mark.asyncio
async def test_flags_disable_only_the_named_capability(evidence_bundle: Any) -> None:
    context = make_context()
    store, chunk, evidence = evidence_bundle
    result = _result_for(chunk, evidence, context)
    retrievers = _fake_registry(context, result)
    reranker = __import__("conftest").FakeReranker()
    service = AgentServiceImpl(
        store,
        retrievers,
        RecordingProvider(),
        reranker=reranker,
        verifier_factory=lambda generate: FakeVerifier(),
    )
    answer = await service.investigate(
        _request(context, features={"enable_graph": False, "enable_expansion": False, "enable_reranker": False})
    )

    trace = service.get_trace(answer.investigation_id)
    assert all((entry.get("selected_action") or {}).get("tool") != "graph" for entry in trace)
    assert all((entry.get("selected_action") or {}).get("query") == "What does build_index do?" for entry in trace if entry.get("selected_action"))
    assert reranker.calls == 0


@pytest.mark.asyncio
async def test_capability_flags_are_independent_and_expansion_is_bounded(evidence_bundle: Any) -> None:
    context = make_context()
    store, chunk, evidence = evidence_bundle
    ledger = EvidenceLedger()
    result = _result_for(chunk, evidence, context)
    await ledger.absorb(result, store, context)
    base_query = make_query(context, "What does graph do?")
    facet = base_query.subquestions[0]
    query = base_query.model_copy(update={"text": "What does graph do in generation snapshots?"})
    state = _state(context).model_copy(
        update={
            "query": query,
            "subquestions": [facet],
            "policy_memory": {
                "remaining_budget_ms": 30_000,
                "remaining_budget_ratio": 1.0,
                "initial_budget_ms": 30_000,
                "initial_max_steps": 6,
                "family_counts": {},
                "support_rows": [
                    {
                        "facet_id": facet.id,
                        "evidence_id": evidence.id,
                        "support": 1.0,
                        "conflict": False,
                        "duplicate_key": chunk.id,
                        "source_kind": "markdown",
                    }
                ],
                "attempts": [],
            },
        }
    )
    tools = {family: FakeRetriever(lambda query: result) for family in ("bm25", "dense", "symbol", "entity", "graph")}
    budget = make_budget(context)

    defaults = _safe_features({})
    graph_off = _safe_features({"enable_graph": False})
    expansion_off = _safe_features({"enable_expansion": False})
    rerank_off = _safe_features({"enable_reranker": False})
    assert graph_off["enable_graph"] is False and graph_off["enable_expansion"] is True
    assert expansion_off["enable_expansion"] is False and expansion_off["enable_graph"] is True
    assert rerank_off["enable_reranker"] is False and rerank_off["enable_graph"] is True
    assert all(value is True for value in defaults.values())

    actions, _ = _build_candidates(state, ledger, tools, defaults, None, budget, 0, 0, True)
    assert any(item.query != facet.text for item in actions)
    assert any(item.tool is ActionTool.rerank for item in actions)
    assert any(item.tool is ActionTool.graph for item in actions)

    graph_actions, _ = _build_candidates(state, ledger, tools, graph_off, None, budget, 0, 0, True)
    expansion_actions, _ = _build_candidates(state, ledger, tools, expansion_off, None, budget, 0, 0, True)
    rerank_actions, _ = _build_candidates(state, ledger, tools, rerank_off, None, budget, 0, 0, True)
    assert not any(item.tool is ActionTool.graph for item in graph_actions)
    assert all(item.query == facet.text for item in expansion_actions)
    assert not any(item.tool is ActionTool.rerank for item in rerank_actions)


@pytest.mark.asyncio
async def test_verifier_finds_code_name_after_sentence_starter(evidence_bundle: Any) -> None:
    context = make_context()
    _, _, evidence = evidence_bundle
    query = make_query(context, "What does build_index do?")

    async def unexpected_model_call(_request: Any) -> Any:
        raise AssertionError("an exact quoted code name should be verified deterministically")

    matrix = await StructuredEvidenceVerifier(unexpected_model_call).evaluate(
        VerifyRequest(query=query, facets=query.subquestions, evidence=[evidence], paths=[])
    )

    assert matrix.rows[0].support == 1.0
    assert matrix.usage.llm_calls == 0


@pytest.mark.asyncio
async def test_structured_verifier_uses_bounded_matrix_and_marks_text_untrusted(evidence_bundle: Any) -> None:
    context = make_context()
    store, _, evidence = evidence_bundle
    del store
    query = make_query(context, "What does this passage establish?")
    provider = RecordingProvider()
    verifier = StructuredEvidenceVerifier(provider.generate)

    matrix = await verifier.evaluate(
        VerifyRequest(query=query, facets=query.subquestions, evidence=[evidence], paths=[])
    )

    assert len(matrix.rows) == 1
    assert matrix.rows[0].support == 1.0
    assert matrix.usage.llm_calls == 1
    assert "untrusted data" in provider.requests[0].system_instruction
    assert "Return only JSON" in provider.requests[0].system_instruction


@pytest.mark.asyncio
async def test_fake_citation_id_is_rejected(evidence_bundle: Any) -> None:
    context = make_context()
    store, chunk, _ = evidence_bundle
    result = _result_for(chunk, next(iter(store.evidence.values())), context)
    provider = RecordingProvider(bad_citations=True)
    service = AgentServiceImpl(
        store,
        _fake_registry(context, result),
        provider,
        verifier_factory=lambda generate: FakeVerifier(),
    )

    answer = await service.investigate(_request(context))

    assert answer.status is AnswerStatus.insufficient
    assert answer.claims == []


@pytest.mark.asyncio
async def test_hard_token_budget_and_no_gain_soft_stop_are_distinct(evidence_bundle: Any) -> None:
    context = make_context()
    empty = RetrievalResult(context=context, hits=[], usage=Usage(count_kind=CountKind.exact))
    provider = RecordingProvider()
    service = AgentServiceImpl(FakeStore({}, {}), _fake_registry(context, empty), provider, verifier_factory=lambda generate: FakeVerifier())

    exhausted = await service.investigate(
        _request(context, budget=make_budget(context, max_total_tokens=64))
    )
    assert exhausted.status is AnswerStatus.insufficient
    assert service.get_trace(exhausted.investigation_id)[-1]["stop_reason"] == StopReason.budget_exhausted.value

    no_gain = await service.investigate(
        _request(context, policy=PolicyKind.rule, budget=make_budget(context, max_steps=6, max_tool_calls=8))
    )
    assert no_gain.status is AnswerStatus.insufficient
    assert service.get_trace(no_gain.investigation_id)[-1]["stop_reason"] == StopReason.repeated_no_gain.value


@pytest.mark.asyncio
async def test_dynamic_stop_flag_can_be_disabled_without_disabling_hard_limits() -> None:
    context = make_context()
    empty = RetrievalResult(context=context, hits=[], usage=Usage(count_kind=CountKind.exact))
    retrievers = _fake_registry(context, empty)
    service = AgentServiceImpl(FakeStore({}, {}), retrievers, RecordingProvider(), verifier_factory=lambda generate: FakeVerifier())
    request = _request(
        context,
        policy=PolicyKind.rule,
        features={"enable_dynamic_stop": False},
        budget=make_budget(context, max_steps=1),
    )

    answer = await service.investigate(request)
    assert answer.status is AnswerStatus.insufficient
    assert service.get_trace(answer.investigation_id)[-1]["stop_reason"] == StopReason.budget_exhausted.value
