from __future__ import annotations

import json
import time
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import networkx as nx

from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError
from knowledge_weaver.core.ids import deterministic_id
from knowledge_weaver.models import (
    CountKind,
    Evidence,
    GraphDirection,
    GraphOptions,
    GraphSnapshot,
    IndexChannel,
    IndexQuery,
    PathStep,
    ProvenanceMethod,
    QueryContext,
    Relation,
    RetrievalHit,
    RetrievalQuery,
    RetrievalResult,
    Usage,
)
from knowledge_weaver.models import (
    Path as GraphPath,
)
from knowledge_weaver.ports import CandidateIndex, GraphStore, KnowledgeStore


@dataclass(frozen=True)
class _SnapshotCacheKey:
    corpus_id: str
    generation_id: str
    source_ids: tuple[str, ...] | None
    include_inferred: bool


@dataclass(frozen=True)
class _GraphView:
    graph: nx.MultiDiGraph
    evidence_by_id: dict[str, Evidence]
    entity_chunk_ids: dict[str, frozenset[str]]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _WalkState:
    entity_ids: tuple[str, ...]
    steps: tuple[PathStep, ...]


def _cache_key(context: QueryContext) -> _SnapshotCacheKey:
    source_ids = (
        None
        if context.source_ids is None
        else tuple(sorted(set(context.source_ids)))
    )
    return _SnapshotCacheKey(
        corpus_id=context.corpus_id,
        generation_id=context.generation_id,
        source_ids=source_ids,
        include_inferred=context.include_inferred,
    )


def _canonical_qualifiers(relation: Relation) -> str:
    return json.dumps(
        relation.qualifiers,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class GraphRetriever:
    """Retrieve bounded, evidence-closed paths from a B03 graph snapshot.

    The EntityIndex only supplies seeds. The graph store remains responsible for
    applying generation and source filters before traversal, and this class
    never picks seeds by scanning all graph entities.
    """

    def __init__(
        self,
        graph_store: GraphStore,
        entity_index: CandidateIndex,
        knowledge_store: KnowledgeStore,
        *,
        cache_capacity: int = 8,
    ) -> None:
        if cache_capacity < 1:
            raise ValueError("cache_capacity must be positive")
        self.graph_store = graph_store
        self.entity_index = entity_index
        self.knowledge_store = knowledge_store
        self.cache_capacity = cache_capacity
        self._snapshot_cache: OrderedDict[_SnapshotCacheKey, _GraphView] = OrderedDict()

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        started = time.perf_counter()
        context = query.context
        if context.deadline_utc <= datetime.now(UTC):
            raise KnowledgeWeaverError(
                ErrorCode.DEADLINE_EXCEEDED,
                "graph retrieval deadline has passed",
                request_id=context.request_id,
            )

        options = query.graph_options or GraphOptions()
        explicit_seeds = list(
            dict.fromkeys([*query.entity_ids, *options.seed_entity_ids])
        )
        warnings: list[str] = []
        candidate_chunk_ids: list[str] = []

        if not explicit_seeds:
            candidates = await self.entity_index.search(
                IndexQuery(
                    channel=IndexChannel.entity,
                    context=context,
                    text=query.text,
                    top_k=query.top_k,
                )
            )
            candidate_chunk_ids = list(
                dict.fromkeys(candidate.chunk_id for candidate in candidates)
            )
            if not candidate_chunk_ids:
                return self._empty_result(context, warnings, started)

        view = await self._get_graph_view(context)
        warnings.extend(view.warnings)

        if explicit_seeds:
            missing = sorted(set(explicit_seeds) - set(view.graph.nodes))
            if missing:
                raise KnowledgeWeaverError(
                    ErrorCode.MISSING_REFERENCE,
                    "requested graph seed is unavailable in this generation or source filter",
                    request_id=context.request_id,
                    details={"missing_entity_ids": missing},
                )
            seeds = explicit_seeds
        else:
            seeds = self._entity_seeds_for_chunks(view, candidate_chunk_ids)
            if not seeds:
                warnings.append("entity index candidates did not resolve to graph entities")
                return self._empty_result(context, warnings, started)

        candidate_paths, partial, examined_edges = self._walk(
            view.graph,
            seeds,
            options,
            request_id=context.request_id,
        )
        if partial:
            warnings.append(
                f"graph edge budget exhausted after {examined_edges} edge examinations; result is partial"
            )

        conflict_count = self._reachable_conflict_count(
            view.graph, {step.relation_id for path in candidate_paths for step in path.steps}
        )
        if conflict_count:
            warnings.append(
                f"retained {conflict_count} explicit positive/negative relation conflict group(s)"
            )

        selected_paths, omitted_paths = self._fit_paths_to_top_k(
            candidate_paths, view.evidence_by_id, query.top_k
        )
        if omitted_paths:
            warnings.append(
                f"omitted {omitted_paths} path(s) whose step evidence would exceed top_k"
            )

        hits = self._hits_from_paths(selected_paths, view.evidence_by_id)
        evidence_ids = list(
            dict.fromkeys(evidence_id for hit in hits for evidence_id in hit.evidence_ids)
        )
        if evidence_ids:
            await self._verify_original_evidence(
                evidence_ids,
                view.evidence_by_id,
                context,
            )

        return RetrievalResult(
            context=context,
            hits=hits,
            paths=selected_paths,
            warnings=warnings,
            partial=partial,
            usage=self._usage(started),
        )
    @property
    def snapshot_cache_size(self) -> int:
        return len(self._snapshot_cache)

    def reset_snapshot_cache(self) -> None:
        """Clear this process's graph-snapshot cache and leave it empty."""
        self._snapshot_cache.clear()

    def has_snapshot_cache(self, context: QueryContext) -> bool:
        return _cache_key(context) in self._snapshot_cache

    async def prewarm_snapshot_cache(self, context: QueryContext) -> bool:
        """Load and verify the graph view used by the next matching retrieval."""
        await self._get_graph_view(context)
        return self.has_snapshot_cache(context)

    async def _get_graph_view(self, context: QueryContext) -> _GraphView:
        key = _cache_key(context)
        cached = self._snapshot_cache.get(key)
        if cached is not None:
            self._snapshot_cache.move_to_end(key)
            return cached

        snapshot = await self.graph_store.snapshot(context)
        if not self._same_scope(snapshot.context, context):
            raise KnowledgeWeaverError(
                ErrorCode.GENERATION_CONFLICT,
                "graph snapshot context does not match the retrieval request",
                request_id=context.request_id,
            )
        view = self._build_graph_view(snapshot, context)
        self._snapshot_cache[key] = view
        self._snapshot_cache.move_to_end(key)
        while len(self._snapshot_cache) > self.cache_capacity:
            self._snapshot_cache.popitem(last=False)
        return view

    @staticmethod
    def _same_scope(left: QueryContext, right: QueryContext) -> bool:
        left_sources = None if left.source_ids is None else tuple(sorted(set(left.source_ids)))
        right_sources = None if right.source_ids is None else tuple(sorted(set(right.source_ids)))
        return (
            left.corpus_id == right.corpus_id
            and left.generation_id == right.generation_id
            and left.request_id == right.request_id
            and left_sources == right_sources
            and left.include_inferred == right.include_inferred
        )

    @staticmethod
    def _build_graph_view(snapshot: GraphSnapshot, context: QueryContext) -> _GraphView:
        graph = nx.MultiDiGraph()
        entity_by_id = {entity.id: entity for entity in snapshot.entities}
        evidence_by_id = {evidence.id: evidence for evidence in snapshot.evidence}
        provenance_by_id = {item.id: item for item in snapshot.provenance}
        entity_sources: dict[str, set[str]] = defaultdict(set)
        entity_chunk_ids: dict[str, set[str]] = defaultdict(set)
        warnings: list[str] = []
        rejected_relations = 0
        allowed_sources = None if context.source_ids is None else set(context.source_ids)
        visible_evidence_ids = {
            evidence_id
            for evidence_id, evidence in evidence_by_id.items()
            if (evidence_provenance := provenance_by_id.get(evidence.provenance_id))
            is not None
            and (
                allowed_sources is None
                or evidence_provenance.source_id in allowed_sources
            )
        }
        for mention in snapshot.mentions:
            evidence = evidence_by_id.get(mention.evidence_id)
            if (
                mention.entity_id not in entity_by_id
                or evidence is None
                or mention.evidence_id not in visible_evidence_ids
            ):
                continue
            mention_provenance = provenance_by_id.get(evidence.provenance_id)
            if mention_provenance is None or (
                allowed_sources is not None
                and mention_provenance.source_id not in allowed_sources
            ):
                continue
            entity_chunk_ids[mention.entity_id].add(evidence.chunk_id)
            entity_sources[mention.entity_id].add(mention_provenance.source_id)
        graph.add_nodes_from(entity_sources)

        for relation in snapshot.relations:
            relation_provenance = provenance_by_id.get(relation.provenance_id)
            if (
                relation.subject_id not in entity_by_id
                or relation.object_id not in entity_by_id
                or relation_provenance is None
                or not relation.evidence_ids
                or any(evidence_id not in visible_evidence_ids for evidence_id in relation.evidence_ids)
                or relation.subject_id not in entity_sources
                or relation.object_id not in entity_sources
            ):
                rejected_relations += 1
                continue

            evidence_provenances = [
                provenance_by_id.get(evidence_by_id[evidence_id].provenance_id)
                for evidence_id in relation.evidence_ids
            ]
            if any(item is None for item in evidence_provenances):
                rejected_relations += 1
                continue
            direct_evidence_ids = [
                evidence_id
                for evidence_id in relation.evidence_ids
                if provenance_by_id[evidence_by_id[evidence_id].provenance_id].method
                != ProvenanceMethod.RULE_INFERRED
            ]
            if not context.include_inferred and (
                relation_provenance.method == ProvenanceMethod.RULE_INFERRED
                or not direct_evidence_ids
            ):
                continue

            subject_sources = entity_sources.get(relation.subject_id, set())
            object_sources = entity_sources.get(relation.object_id, set())
            cross_source = any(
                subject_source != object_source
                for subject_source in subject_sources
                for object_source in object_sources
            )
            if cross_source and not direct_evidence_ids:
                rejected_relations += 1
                continue

            traversable_evidence_ids = (
                list(relation.evidence_ids)
                if context.include_inferred
                else direct_evidence_ids
            )
            if not traversable_evidence_ids:
                rejected_relations += 1
                continue

            graph.add_edge(
                relation.subject_id,
                relation.object_id,
                key=relation.id,
                relation=relation,
                predicate=relation.predicate.value,
                polarity=relation.polarity.value,
                qualifiers=relation.qualifiers,
                canonical_qualifiers=_canonical_qualifiers(relation),
                provenance_id=relation.provenance_id,
                provenance_method=relation_provenance.method.value,
                source_id=relation_provenance.source_id,
                evidence_ids=tuple(traversable_evidence_ids),
                cross_source=cross_source,
            )

        if rejected_relations:
            warnings.append(
                f"rejected {rejected_relations} graph relation(s) with incomplete references or unsupported cross-source evidence"
            )
        return _GraphView(
            graph=graph,
            evidence_by_id=evidence_by_id,
            entity_chunk_ids={
                entity_id: frozenset(chunk_ids)
                for entity_id, chunk_ids in entity_chunk_ids.items()
            },
            warnings=tuple(warnings),
        )

    @staticmethod
    def _entity_seeds_for_chunks(view: _GraphView, chunk_ids: list[str]) -> list[str]:
        entities: list[str] = []
        seen: set[str] = set()
        for chunk_id in chunk_ids:
            for entity_id in sorted(view.entity_chunk_ids):
                if chunk_id in view.entity_chunk_ids[entity_id] and entity_id not in seen:
                    entities.append(entity_id)
                    seen.add(entity_id)
        return entities

    @staticmethod
    def _walk(
        graph: nx.MultiDiGraph,
        seeds: list[str],
        options: GraphOptions,
        *,
        request_id: str,
    ) -> tuple[list[GraphPath], bool, int]:
        queue: deque[_WalkState] = deque(
            _WalkState(entity_ids=(seed,), steps=()) for seed in seeds
        )
        paths: list[GraphPath] = []
        seen_paths: set[tuple[tuple[str, ...], tuple[tuple[str, bool], ...]]] = set()
        examined_edges = 0
        partial = False

        while queue:
            state = queue.popleft()
            if len(state.steps) >= options.max_hops:
                continue
            current = state.entity_ids[-1]
            candidates = GraphRetriever._adjacent_edges(graph, current, options.direction)
            if options.predicates:
                predicates = {item.value for item in options.predicates}
                candidates = [
                    item for item in candidates if item[3].get("predicate") in predicates
                ]
            for next_entity, relation_id, reverse, edge_data in candidates:
                if examined_edges >= options.max_edges:
                    partial = True
                    break
                examined_edges += 1
                if next_entity in state.entity_ids:
                    continue
                next_ids = (*state.entity_ids, next_entity)
                next_steps = (
                    *state.steps,
                    PathStep(
                        relation_id=relation_id,
                        from_id=current,
                        to_id=next_entity,
                        traversed_reverse=reverse,
                        evidence_ids=list(edge_data["evidence_ids"]),
                    ),
                )
                path_key = (
                    next_ids,
                    tuple((step.relation_id, step.traversed_reverse) for step in next_steps),
                )
                if path_key in seen_paths:
                    continue
                seen_paths.add(path_key)
                paths.append(
                    GraphPath(
                        id=deterministic_id(
                            "graph_path",
                            {
                                "entity_ids": next_ids,
                                "steps": [
                                    [step.relation_id, step.traversed_reverse]
                                    for step in next_steps
                                ],
                            },
                        ),
                        entity_ids=list(next_ids),
                        steps=list(next_steps),
                    )
                )
                queue.append(_WalkState(entity_ids=next_ids, steps=next_steps))
            if partial:
                break

        if partial and examined_edges > options.max_edges:
            raise KnowledgeWeaverError(
                ErrorCode.INTERNAL_ERROR,
                "graph traversal exceeded its configured edge budget",
                request_id=request_id,
            )
        return paths, partial, examined_edges

    @staticmethod
    def _adjacent_edges(
        graph: nx.MultiDiGraph, entity_id: str, direction: GraphDirection
    ) -> list[tuple[str, str, bool, dict[str, Any]]]:
        adjacent: list[tuple[str, str, bool, dict[str, Any]]] = []
        if direction in {GraphDirection.out, GraphDirection.both}:
            for _, neighbor, relation_id, data in graph.out_edges(
                entity_id, keys=True, data=True
            ):
                adjacent.append((neighbor, relation_id, False, data))
        if direction in {GraphDirection.incoming, GraphDirection.both}:
            for neighbor, _, relation_id, data in graph.in_edges(
                entity_id, keys=True, data=True
            ):
                adjacent.append((neighbor, relation_id, True, data))
        adjacent.sort(key=lambda item: (item[0], item[1], item[2]))
        return adjacent

    @staticmethod
    def _reachable_conflict_count(
        graph: nx.MultiDiGraph, reachable_relation_ids: set[str]
    ) -> int:
        grouped: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
        for _, _, relation_id, data in graph.edges(keys=True, data=True):
            if relation_id not in reachable_relation_ids:
                continue
            if data["provenance_method"] == ProvenanceMethod.RULE_INFERRED.value:
                continue
            relation = data["relation"]
            key = (
                relation.subject_id,
                relation.predicate.value,
                relation.object_id,
                data["canonical_qualifiers"],
            )
            grouped[key].add(relation.polarity.value)
        return sum(
            1
            for polarities in grouped.values()
            if {"positive", "negative"}.issubset(polarities)
        )

    @staticmethod
    def _fit_paths_to_top_k(
        paths: list[GraphPath], evidence_by_id: dict[str, Evidence], top_k: int
    ) -> tuple[list[GraphPath], int]:
        ordered = sorted(
            paths,
            key=lambda path: (
                -(1.0 / (1 + len(path.steps))),
                path.id,
            ),
        )
        selected: list[GraphPath] = []
        selected_chunks: set[str] = set()
        omitted = 0
        for path in ordered:
            path_chunks = {
                evidence_by_id[evidence_id].chunk_id
                for step in path.steps
                for evidence_id in step.evidence_ids
                if evidence_id in evidence_by_id
            }
            if not path_chunks:
                omitted += 1
                continue
            if len(selected_chunks | path_chunks) > top_k:
                omitted += 1
                continue
            selected.append(path)
            selected_chunks.update(path_chunks)
        selected.sort(key=lambda path: (len(path.steps), path.id))
        return selected, omitted

    @staticmethod
    def _hits_from_paths(
        paths: list[GraphPath], evidence_by_id: dict[str, Evidence]
    ) -> list[RetrievalHit]:
        by_chunk: dict[str, dict[str, Any]] = {}
        for path in paths:
            path_score = 1.0 / (1 + len(path.steps))
            for step in path.steps:
                for evidence_id in step.evidence_ids:
                    evidence = evidence_by_id.get(evidence_id)
                    if evidence is None:
                        continue
                    entry = by_chunk.setdefault(
                        evidence.chunk_id,
                        {"score": 0.0, "evidence_ids": set(), "path_ids": set()},
                    )
                    entry["score"] = max(entry["score"], path_score)
                    entry["evidence_ids"].add(evidence_id)
                    entry["path_ids"].add(path.id)
        ordered = sorted(by_chunk.items(), key=lambda item: (-item[1]["score"], item[0]))
        return [
            RetrievalHit(
                chunk_id=chunk_id,
                evidence_ids=sorted(entry["evidence_ids"]),
                score=entry["score"],
                channel="graph",
                rank=rank,
                path_ids=sorted(entry["path_ids"]),
            )
            for rank, (chunk_id, entry) in enumerate(ordered, 1)
        ]

    async def _verify_original_evidence(
        self,
        evidence_ids: list[str],
        snapshot_evidence: dict[str, Evidence],
        context: QueryContext,
    ) -> None:
        hydrated = await self.knowledge_store.get_evidence(evidence_ids, context)
        hydrated_by_id = {item.id: item for item in hydrated}
        missing = sorted(set(evidence_ids) - set(hydrated_by_id))
        if missing:
            raise KnowledgeWeaverError(
                ErrorCode.MISSING_REFERENCE,
                "graph path evidence is unavailable in the requested generation",
                request_id=context.request_id,
                details={"missing_evidence_ids": missing},
            )
        for evidence_id in evidence_ids:
            if hydrated_by_id[evidence_id] != snapshot_evidence[evidence_id]:
                raise KnowledgeWeaverError(
                    ErrorCode.SCHEMA_INVALID,
                    "graph snapshot evidence differs from stored evidence",
                    request_id=context.request_id,
                    details={"evidence_id": evidence_id},
                )

        chunk_ids = list(
            dict.fromkeys(hydrated_by_id[evidence_id].chunk_id for evidence_id in evidence_ids)
        )
        chunks = await self.knowledge_store.get_chunks(chunk_ids, context)
        chunks_by_id = {chunk.id: chunk for chunk in chunks}
        missing_chunks = sorted(set(chunk_ids) - set(chunks_by_id))
        if missing_chunks:
            raise KnowledgeWeaverError(
                ErrorCode.MISSING_REFERENCE,
                "graph path chunk is unavailable in the requested generation",
                request_id=context.request_id,
                details={"missing_chunk_ids": missing_chunks},
            )
        for evidence_id in evidence_ids:
            evidence = hydrated_by_id[evidence_id]
            try:
                evidence.assert_matches_chunk(chunks_by_id[evidence.chunk_id])
            except ValueError as exc:
                raise KnowledgeWeaverError(
                    ErrorCode.SCHEMA_INVALID,
                    "graph path evidence does not match its source-backed chunk text",
                    request_id=context.request_id,
                    details={"evidence_id": evidence_id},
                ) from exc

    @staticmethod
    def _usage(started: float) -> Usage:
        elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
        return Usage(elapsed_ms=elapsed_ms, count_kind=CountKind.unavailable)

    def _empty_result(
        self, context: QueryContext, warnings: list[str], started: float
    ) -> RetrievalResult:
        return RetrievalResult(
            context=context,
            hits=[],
            paths=[],
            warnings=warnings,
            partial=False,
            usage=self._usage(started),
        )
