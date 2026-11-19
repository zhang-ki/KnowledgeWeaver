"""Hand-computable benchmark metrics with duplicate evidence groups counted once."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalMetrics:
    precision_at_k: dict[int, float]
    recall_at_k: dict[int, float]
    ndcg_at_k: dict[int, float]
    reciprocal_rank: float
    relevant_group_count: int


@dataclass(frozen=True)
class CitationJudgment:
    supports_claim: bool
    resolves_to_original: bool


@dataclass(frozen=True)
class CitationMetrics:
    precision: float | None
    validity: float | None
    citation_count: int


@dataclass(frozen=True)
class NoAnswerMetrics:
    case_count: int
    unanswerable_count: int
    refusal_accuracy: float | None
    false_citation_rate: float | None


@dataclass(frozen=True)
class MultihopPathMetrics:
    complete_path_accuracy: float
    gold_path_coverage: float
    expected_path_count: int
    matched_path_count: int


def retrieval_metrics(
    ranked_groups: Sequence[str],
    gold_grades: Mapping[str, int],
    ks: Sequence[int] = (5, 10, 20),
) -> RetrievalMetrics:
    """Compute Precision@K, Recall@K, MRR, and nDCG over claim groups."""
    if not gold_grades:
        raise ValueError("retrieval metrics require at least one answerable Gold group")
    if any(not key or grade not in {1, 2, 3} for key, grade in gold_grades.items()):
        raise ValueError("Gold groups need non-empty IDs and grades in [1, 3]")
    if not ks or any(k <= 0 for k in ks) or len(set(ks)) != len(ks):
        raise ValueError("K values must be unique positive integers")

    seen: set[str] = set()
    first_relevant_rank: int | None = None
    gains: list[float] = []
    hits: list[bool] = []
    for rank, group_id in enumerate(ranked_groups, start=1):
        first_occurrence = group_id not in seen
        seen.add(group_id)
        grade = gold_grades.get(group_id, 0) if first_occurrence else 0
        gains.append(float(2**grade - 1) if grade else 0.0)
        hits.append(grade > 0)
        if grade > 0 and first_relevant_rank is None:
            first_relevant_rank = rank

    ideal_gains = sorted((float(2**grade - 1) for grade in gold_grades.values()), reverse=True)
    precision: dict[int, float] = {}
    recall: dict[int, float] = {}
    ndcg: dict[int, float] = {}
    for k in ks:
        precision[k] = sum(hits[:k]) / k
        recall[k] = sum(hits[:k]) / len(gold_grades)
        dcg = sum(gain / math.log2(rank + 2) for rank, gain in enumerate(gains[:k]))
        ideal_dcg = sum(
            gain / math.log2(rank + 2) for rank, gain in enumerate(ideal_gains[:k])
        )
        ndcg[k] = dcg / ideal_dcg if ideal_dcg else 0.0

    return RetrievalMetrics(
        precision_at_k=precision,
        recall_at_k=recall,
        ndcg_at_k=ndcg,
        reciprocal_rank=1.0 / first_relevant_rank if first_relevant_rank else 0.0,
        relevant_group_count=len(gold_grades),
    )


def evidence_recall(returned_groups: Sequence[str], required_groups: Sequence[str]) -> float:
    """Measure answer evidence coverage over all required Gold claim groups."""
    required = set(required_groups)
    if not required:
        raise ValueError("evidence recall requires at least one answerable Gold group")
    return len(set(returned_groups) & required) / len(required)


def citation_metrics(
    citations: Sequence[CitationJudgment], *, answerable: bool, abstained: bool
) -> CitationMetrics:
    """Keep semantic support and citation resolvability as separate measures."""
    if not citations:
        precision = 0.0 if answerable else None
        return CitationMetrics(precision=precision, validity=None, citation_count=0)
    return CitationMetrics(
        precision=sum(citation.supports_claim for citation in citations) / len(citations),
        validity=sum(citation.resolves_to_original for citation in citations) / len(citations),
        citation_count=len(citations),
    )


def no_answer_metrics(
    outcomes: Sequence[tuple[bool, bool, int]],
) -> NoAnswerMetrics:
    """Return refusal accuracy and false-citation rate for unanswerable cases.

    Each outcome is ``(is_answerable, system_abstained, citation_count)``. Retrieval
    quality metrics intentionally exclude unanswerable cases.
    """
    if any(citation_count < 0 for _answerable, _abstained, citation_count in outcomes):
        raise ValueError("citation counts cannot be negative")
    unanswerable = [outcome for outcome in outcomes if not outcome[0]]
    if not unanswerable:
        return NoAnswerMetrics(len(outcomes), 0, None, None)
    return NoAnswerMetrics(
        case_count=len(outcomes),
        unanswerable_count=len(unanswerable),
        refusal_accuracy=sum(abstained for _answerable, abstained, _count in unanswerable)
        / len(unanswerable),
        false_citation_rate=sum(count > 0 for _answerable, _abstained, count in unanswerable)
        / len(unanswerable),
    )


def multihop_path_metrics(
    expected_paths: Sequence[Sequence[str]],
    retrieved_paths: Sequence[Sequence[str]],
) -> MultihopPathMetrics:
    """Match complete ordered paths; edge tokens must include direction and evidence IDs."""
    expected_list = [tuple(path) for path in expected_paths]
    expected = set(expected_list)
    if not expected_list or any(not path for path in expected_list):
        raise ValueError("multihop metrics require non-empty Gold paths")
    if len(expected) != len(expected_list):
        raise ValueError("Gold paths must be unique")
    retrieved = {tuple(path) for path in retrieved_paths if path}
    matched = len(expected & retrieved)
    return MultihopPathMetrics(
        complete_path_accuracy=float(matched > 0),
        gold_path_coverage=matched / len(expected),
        expected_path_count=len(expected),
        matched_path_count=matched,
    )
