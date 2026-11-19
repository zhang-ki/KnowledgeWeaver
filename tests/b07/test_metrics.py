from __future__ import annotations

import pytest

from knowledge_weaver.evaluation.metrics import (
    CitationJudgment,
    citation_metrics,
    evidence_recall,
    multihop_path_metrics,
    no_answer_metrics,
    retrieval_metrics,
)


def test_retrieval_metrics_are_hand_computable_and_deduplicate_groups() -> None:
    result = retrieval_metrics(
        ["g1", "g1", "noise", "g2"],
        {"g1": 3, "g2": 1},
        ks=(1, 2, 4),
    )

    assert result.precision_at_k == {1: 1.0, 2: 0.5, 4: 0.5}
    assert result.recall_at_k == {1: 0.5, 2: 0.5, 4: 1.0}
    assert result.reciprocal_rank == 1.0
    assert result.ndcg_at_k[1] == 1.0
    assert result.ndcg_at_k[2] == pytest.approx(7 / (7 + 1 / 1.584962500721156))


def test_retrieval_metrics_reject_unanswerable_denominators_and_bad_k() -> None:
    with pytest.raises(ValueError, match="at least one answerable"):
        retrieval_metrics(["g1"], {})
    with pytest.raises(ValueError, match="positive integers"):
        retrieval_metrics(["g1"], {"g1": 1}, ks=(5, 5))


def test_evidence_recall_covers_all_required_groups_once() -> None:
    assert evidence_recall(["g1", "g1", "noise"], ["g1", "g2"]) == 0.5
    with pytest.raises(ValueError, match="answerable Gold"):
        evidence_recall([], [])


def test_citation_support_and_validity_are_separate() -> None:
    result = citation_metrics(
        [
            CitationJudgment(supports_claim=True, resolves_to_original=True),
            CitationJudgment(supports_claim=False, resolves_to_original=True),
            CitationJudgment(supports_claim=False, resolves_to_original=False),
        ],
        answerable=True,
        abstained=False,
    )

    assert result.precision == pytest.approx(1 / 3)
    assert result.validity == pytest.approx(2 / 3)


def test_unanswerable_refusal_and_false_citation_rates_are_separate() -> None:
    result = no_answer_metrics(
        [
            (False, True, 0),
            (False, False, 1),
            (True, False, 2),
        ]
    )

    assert result.unanswerable_count == 2
    assert result.refusal_accuracy == 0.5
    assert result.false_citation_rate == 0.5


def test_correct_abstention_without_citations_is_not_a_zero_precision_case() -> None:
    result = citation_metrics([], answerable=False, abstained=True)
    missing_answer = citation_metrics([], answerable=True, abstained=True)

    assert result.precision is None
    assert result.validity is None
    assert missing_answer.precision == 0.0


def test_multihop_metric_requires_a_complete_ordered_directional_path() -> None:
    result = multihop_path_metrics(
        expected_paths=[
            ["rel-1:forward:evidence-A", "rel-2:reverse:evidence-B"],
            ["rel-3:forward:evidence-C"],
        ],
        retrieved_paths=[
            ["rel-1:forward:evidence-A"],
            ["rel-1:forward:evidence-A", "rel-2:reverse:evidence-B"],
        ],
    )

    assert result.complete_path_accuracy == 1.0
    assert result.gold_path_coverage == 0.5
    assert result.expected_path_count == 2
    assert result.matched_path_count == 1
