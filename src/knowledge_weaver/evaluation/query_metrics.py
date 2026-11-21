"""Per-query metrics joining ranked generation chunks to reviewed Gold mappings."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from knowledge_weaver.evaluation.gold_mapping import (
    GoldMappingReport,
    GoldMappingStatus,
    GoldPathMapping,
)
from knowledge_weaver.evaluation.metrics import (
    CitationJudgment,
    citation_metrics,
    no_answer_metrics,
    retrieval_metrics,
)
from knowledge_weaver.evaluation.runs import QueryMeasurements
from knowledge_weaver.models.contracts import BenchmarkCase, Path


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _path_matches(expected: GoldPathMapping, actual: Path) -> bool:
    if expected.status is not GoldMappingStatus.mapped:
        return False
    if len(expected.relation_ids) != len(actual.steps):
        return False
    for index, step in enumerate(actual.steps):
        if step.relation_id != expected.relation_ids[index]:
            return False
        if step.traversed_reverse != expected.traversed_reverse[index]:
            return False
        if not set(expected.evidence_ids_by_edge[index]).issubset(step.evidence_ids):
            return False
    return True


def compute_query_measurements(
    case: BenchmarkCase,
    mapping_report: GoldMappingReport,
    *,
    ranked_chunk_ids: Sequence[str],
    retrieved_paths: Sequence[Path] = (),
    citations: Sequence[CitationJudgment] = (),
    citation_count: int | None = None,
    abstained: bool,
    answer_correctness: Literal[0, 1, 2] | None = None,
    human_reviewer_id: str | None = None,
    human_reviewed_at: datetime | None = None,
) -> QueryMeasurements:
    """Compute separate claim-group, Gold-evidence-group, and strict-chunk metrics."""
    if mapping_report.blockers:
        raise ValueError("Gold mapping blockers prevent query-level quality calculation")
    if citation_count is None:
        citation_count = len(citations)
    if citation_count < len(citations):
        raise ValueError("citation_count cannot be smaller than the reviewed citation set")
    chunks = _unique(ranked_chunk_ids)
    case_spans = [item for item in mapping_report.span_mappings if item.case_id == case.id]
    case_paths = [item for item in mapping_report.path_mappings if item.case_id == case.id]
    if case.answerable and len(case_spans) != len(case.gold):
        raise ValueError("every answerable Gold span must have a generation mapping")
    if any(item.status is not GoldMappingStatus.mapped for item in case_spans + case_paths):
        raise ValueError("unmapped Gold spans and paths cannot be omitted from metrics")

    precision_at_k: dict[int, float] = {}
    recall_at_k: dict[int, float] = {}
    ndcg_at_k: dict[int, float] = {}
    evidence_group_recall_at_k: dict[int, float] = {}
    strict_chunk_recall_at_k: dict[int, float] = {}
    reciprocal_rank: float | None = None
    evidence_recall_value: float | None = None
    strict_chunk_recall_value: float | None = None

    if case.answerable and case_spans:
        grades: dict[str, int] = {}
        chunks_by_claim: dict[str, set[str]] = defaultdict(set)
        chunks_by_evidence_group: dict[str, set[str]] = defaultdict(set)
        for gold, mapped in zip(case.gold, case_spans, strict=True):
            grades[gold.claim_group] = max(grades.get(gold.claim_group, 0), gold.grade)
            chunks_by_claim[gold.claim_group].update(mapped.chunk_ids)
            chunks_by_evidence_group[mapped.evidence_group_id].update(mapped.chunk_ids)
        ranked_claim_groups = [
            claim_group
            for chunk_id in chunks
            for claim_group in sorted(
                claim_group
                for claim_group, gold_chunks in chunks_by_claim.items()
                if chunk_id in gold_chunks
            )
        ]
        retrieval = retrieval_metrics(ranked_claim_groups, grades)
        precision_at_k = retrieval.precision_at_k
        recall_at_k = retrieval.recall_at_k
        ndcg_at_k = retrieval.ndcg_at_k
        reciprocal_rank = retrieval.reciprocal_rank
        required_claim_groups = set(chunks_by_claim)
        returned_claim_groups = {
            group
            for chunk_id in chunks
            for group, gold_chunks in chunks_by_claim.items()
            if chunk_id in gold_chunks
        }
        evidence_recall_value = (
            len(required_claim_groups & returned_claim_groups) / len(required_claim_groups)
        )
        all_gold_chunks = {chunk for values in chunks_by_claim.values() for chunk in values}
        for k in (5, 10, 20):
            top_chunks = set(chunks[:k])
            evidence_group_recall_at_k[k] = sum(
                bool(top_chunks & values) for values in chunks_by_evidence_group.values()
            ) / len(chunks_by_evidence_group)
            strict_chunk_recall_at_k[k] = len(top_chunks & all_gold_chunks) / len(all_gold_chunks)
        strict_chunk_recall_value = strict_chunk_recall_at_k[10]

    citation = citation_metrics(citations, answerable=case.answerable, abstained=abstained)
    no_answer = no_answer_metrics([(case.answerable, abstained, citation_count)])

    complete_path_accuracy: float | None = None
    gold_path_coverage: float | None = None
    if case_paths:
        matches = sum(
            any(_path_matches(expected, actual) for actual in retrieved_paths)
            for expected in case_paths
        )
        complete_path_accuracy = float(matches > 0)
        gold_path_coverage = matches / len(case_paths)

    return QueryMeasurements(
        retrieval_precision_at_k=precision_at_k,
        retrieval_recall_at_k=recall_at_k,
        retrieval_ndcg_at_k=ndcg_at_k,
        gold_evidence_group_recall_at_k=evidence_group_recall_at_k,
        strict_chunk_recall_at_k=strict_chunk_recall_at_k,
        reciprocal_rank=reciprocal_rank,
        evidence_recall=evidence_recall_value,
        strict_chunk_recall=strict_chunk_recall_value,
        citation_precision=citation.precision,
        citation_validity=citation.validity,
        unanswerable_refusal_accuracy=no_answer.refusal_accuracy,
        unanswerable_false_citation_rate=(
            no_answer.false_citation_rate
            if not case.answerable
            else None
        ),
        complete_path_accuracy=complete_path_accuracy,
        gold_path_coverage=gold_path_coverage,
        answer_correctness=answer_correctness,
        human_reviewer_id=human_reviewer_id,
        human_reviewed_at=human_reviewed_at,
    )
