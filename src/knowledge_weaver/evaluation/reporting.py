"""Deterministic B07 metrics, usage, failure, bootstrap, and Pareto reports."""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from knowledge_weaver.evaluation.runs import QueryRunRecord
from knowledge_weaver.evaluation.statistics import (
    ParetoPoint,
    paired_source_family_bootstrap,
    pareto_frontier,
)


def _mean(values: Sequence[float | int]) -> float | None:
    return statistics.fmean(values) if values else None


def _percentile(values: Sequence[int], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _decimal_sum(values: Sequence[Decimal | None]) -> str | None:
    if not values or any(value is None for value in values):
        return None
    return str(sum((value for value in values if value is not None), Decimal(0)))


def _quality_value(record: QueryRunRecord) -> float | None:
    metrics = record.measurements
    if (
        not record.formal_quality_eligible
        or metrics is None
        or metrics.evidence_recall is None
        or metrics.answer_correctness is None
    ):
        return None
    return (metrics.evidence_recall + metrics.answer_correctness / 2.0) / 2.0


def summarize_query_runs(records: Sequence[QueryRunRecord]) -> dict[str, Any]:
    """Aggregate without dropping failed attempts or treating fixture outputs as quality."""
    by_suite: dict[tuple[str, str, str, str | None, str], list[QueryRunRecord]] = defaultdict(list)
    for record in records:
        by_suite[
            (
                record.suite_id,
                record.system_id,
                record.budget_id,
                record.ablation_id,
                record.cache_state,
            )
        ].append(record)

    summaries: list[dict[str, Any]] = []
    for (suite_id, system_id, budget_id, ablation_id, cache_state), rows in sorted(by_suite.items()):
        formal_rows = [row for row in rows if row.formal_quality_eligible]
        measurements = [row.measurements for row in formal_rows if row.measurements is not None]
        summary: dict[str, Any] = {
            "suite_id": suite_id,
            "system_id": system_id,
            "budget_id": budget_id,
            "ablation_id": ablation_id,
            "cache_state": cache_state,
            "query_count": len(rows),
            "failed_query_count": sum(row.status != "complete" for row in rows),
            "formal_quality_query_count": len(formal_rows),
            "total_input_tokens": sum(row.input_tokens for row in rows),
            "total_output_tokens": sum(row.output_tokens for row in rows),
            "total_tokens": sum(row.total_tokens for row in rows),
            "tool_calls": sum(row.tool_calls for row in rows),
            "llm_calls": sum(row.llm_calls for row in rows),
            "end_to_end_latency_p50_ms": _percentile(
                [row.end_to_end_latency_ms for row in rows], 0.5
            ),
            "end_to_end_latency_p95_ms": _percentile(
                [row.end_to_end_latency_ms for row in rows], 0.95
            ),
            "strategy_latency_p50_ms": _percentile(
                [row.strategy_latency_ms for row in rows], 0.5
            ),
            "strategy_latency_p95_ms": _percentile(
                [row.strategy_latency_ms for row in rows], 0.95
            ),
            "currency_cost_usd": _decimal_sum([row.currency_cost_usd for row in rows]),
            "currency_cost_cny": _decimal_sum([row.currency_cost_cny for row in rows]),
            "mean_evidence_recall": _mean(
                [metric.evidence_recall for metric in measurements if metric.evidence_recall is not None]
            ),
            "retrieval_precision_at_k": {
                k: _mean([metric.retrieval_precision_at_k[k] for metric in measurements if k in metric.retrieval_precision_at_k])
                for k in (5, 10, 20)
            },
            "retrieval_recall_at_k": {
                k: _mean([metric.retrieval_recall_at_k[k] for metric in measurements if k in metric.retrieval_recall_at_k])
                for k in (5, 10, 20)
            },
            "retrieval_ndcg_at_k": {
                k: _mean([metric.retrieval_ndcg_at_k[k] for metric in measurements if k in metric.retrieval_ndcg_at_k])
                for k in (5, 10, 20)
            },
            "gold_evidence_group_recall_at_k": {
                k: _mean([metric.gold_evidence_group_recall_at_k[k] for metric in measurements if k in metric.gold_evidence_group_recall_at_k])
                for k in (5, 10, 20)
            },
            "strict_chunk_recall_at_k": {
                k: _mean([metric.strict_chunk_recall_at_k[k] for metric in measurements if k in metric.strict_chunk_recall_at_k])
                for k in (5, 10, 20)
            },
            "mean_mrr": _mean(
                [metric.reciprocal_rank for metric in measurements if metric.reciprocal_rank is not None]
            ),
            "mean_strict_chunk_recall": _mean(
                [metric.strict_chunk_recall for metric in measurements if metric.strict_chunk_recall is not None]
            ),
            "mean_citation_precision": _mean(
                [metric.citation_precision for metric in measurements if metric.citation_precision is not None]
            ),
            "mean_citation_validity": _mean(
                [metric.citation_validity for metric in measurements if metric.citation_validity is not None]
            ),
            "mean_complete_path_accuracy": _mean(
                [metric.complete_path_accuracy for metric in measurements if metric.complete_path_accuracy is not None]
            ),
            "mean_gold_path_coverage": _mean(
                [metric.gold_path_coverage for metric in measurements if metric.gold_path_coverage is not None]
            ),
            "unanswerable_refusal_accuracy": _mean(
                [metric.unanswerable_refusal_accuracy for metric in measurements if metric.unanswerable_refusal_accuracy is not None]
            ),
            "unanswerable_false_citation_rate": _mean(
                [metric.unanswerable_false_citation_rate for metric in measurements if metric.unanswerable_false_citation_rate is not None]
            ),
            "answer_correctness_mean_0_to_2": _mean(
                [metric.answer_correctness for metric in measurements if metric.answer_correctness is not None]
            ),
            "mean_quality_score": _mean(
                [value for value in (_quality_value(row) for row in formal_rows) if value is not None]
            ),
        }
        summaries.append(summary)
    return {"suite_summaries": summaries, "query_record_count": len(records)}


def _seed_averaged_metric(
    records: Sequence[QueryRunRecord], *, system_id: str, budget_id: str, metric_name: str
) -> tuple[dict[str, float], dict[str, tuple[str, ...]]]:
    by_case: dict[str, list[tuple[int, str, float, tuple[str, ...]]]] = defaultdict(list)
    for record in records:
        if (
            record.system_id != system_id
            or record.budget_id != budget_id
            or not record.formal_quality_eligible
            or record.measurements is None
        ):
            continue
        value = (
            _quality_value(record)
            if metric_name == "quality_score"
            else getattr(record.measurements, metric_name)
        )
        if value is not None:
            by_case[record.case_id].append(
                (record.seed, record.cache_state, float(value), tuple(record.source_family_ids))
            )
    values: dict[str, float] = {}
    families: dict[str, tuple[str, ...]] = {}
    expected_profiles = {
        (seed, cache_state)
        for seed in (17, 29, 43)
        for cache_state in ("cold", "warm")
    }
    for case_id, rows in by_case.items():
        profiles = {(seed, cache_state) for seed, cache_state, _value, _families in rows}
        if profiles != expected_profiles:
            continue
        family_values = {
            tuple(sorted(family_ids))
            for _seed, _cache_state, _value, family_ids in rows
        }
        if len(family_values) != 1:
            raise ValueError("source-family attribution changed across fixed seeds")
        values[case_id] = statistics.fmean(
            value for _seed, _cache_state, value, _families in rows
        )
        families[case_id] = next(iter(family_values))
    return values, families


def paired_system_comparisons(
    records: Sequence[QueryRunRecord], *, baseline: str = "hybrid_rerank", budget_id: str = "standard"
) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    systems = sorted({row.system_id for row in records if row.ablation_id is None})
    metrics = ("evidence_recall", "answer_correctness", "quality_score")
    for challenger in systems:
        if challenger == baseline:
            continue
        for metric in metrics:
            left, left_families = _seed_averaged_metric(
                records, system_id=challenger, budget_id=budget_id, metric_name=metric
            )
            right, right_families = _seed_averaged_metric(
                records, system_id=baseline, budget_id=budget_id, metric_name=metric
            )
            common = sorted(set(left) & set(right))
            if len(common) < 2:
                continue
            family_map = {
                case_id: left_families[case_id]
                for case_id in common
            }
            if any(family_map[case_id] != right_families[case_id] for case_id in common):
                raise ValueError("paired systems disagree on source-family attribution")
            ci = paired_source_family_bootstrap(
                {case_id: left[case_id] for case_id in common},
                {case_id: right[case_id] for case_id in common},
                family_map,
            )
            comparisons.append(
                {
                    "challenger": challenger,
                    "baseline": baseline,
                    "budget_id": budget_id,
                    "metric": metric,
                    **ci.__dict__,
                }
            )
    return comparisons


def system_pareto_frontier(
    records: Sequence[QueryRunRecord], *, budget_id: str = "standard"
) -> list[dict[str, Any]]:
    grouped: dict[str, list[QueryRunRecord]] = defaultdict(list)
    for record in records:
        if record.budget_id == budget_id and record.ablation_id is None:
            grouped[record.system_id].append(record)
    points: list[ParetoPoint] = []
    for system_id, rows in grouped.items():
        eligible = [row for row in rows if _quality_value(row) is not None]
        if not eligible:
            continue
        currency_values = [row.currency_cost_usd for row in rows]
        currency_cost = (
            float(sum((item for item in currency_values if item is not None), Decimal(0)))
            if currency_values and all(item is not None for item in currency_values)
            else None
        )
        points.append(
            ParetoPoint(
                system_id=system_id,
                quality=statistics.fmean(_quality_value(row) or 0.0 for row in eligible),
                total_tokens=round(statistics.fmean(row.total_tokens for row in rows)),
                latency_ms=round(statistics.fmean(row.end_to_end_latency_ms for row in rows)),
                currency_cost=currency_cost,
            )
        )
    frontier = pareto_frontier(points)
    return [point.__dict__ for point in frontier]


def _validate_query_coverage(
    matrix_manifest: Mapping[str, Any],
    schedule: Mapping[str, Any],
    records: Sequence[QueryRunRecord],
    *,
    status: str,
) -> None:
    raw_suites = schedule.get("suites", [])
    if not isinstance(raw_suites, list):
        raise TypeError("matrix schedule suites must be a list")
    suites: dict[str, Mapping[str, Any]] = {}
    for suite in raw_suites:
        if not isinstance(suite, Mapping) or not isinstance(suite.get("suite_id"), str):
            raise TypeError("matrix schedule contains an invalid suite identity")
        suite_id = suite["suite_id"]
        if suite_id in suites:
            raise ValueError("matrix schedule contains duplicate suite identities")
        suites[suite_id] = suite

    raw_expected = matrix_manifest.get("expected_query_keys")
    expected_keys: set[tuple[str, str, str]] = set()
    expected_counts: dict[str, int] = defaultdict(int)
    if raw_expected is not None:
        if not isinstance(raw_expected, list):
            raise TypeError("expected query keys must be a list")
        for item in raw_expected:
            if not isinstance(item, Mapping):
                raise TypeError("expected query key is malformed")
            suite_id, case_id, cache_state = (
                item.get("suite_id"),
                item.get("case_id"),
                item.get("cache_state"),
            )
            if (
                not isinstance(suite_id, str)
                or not isinstance(case_id, str)
                or not isinstance(cache_state, str)
                or cache_state not in {"cold", "warm"}
            ):
                raise TypeError("expected query key is malformed")
            key = (suite_id, case_id, cache_state)
            if key in expected_keys:
                raise ValueError("expected query keys contain a duplicate")
            if suite_id not in suites:
                raise ValueError("expected query key references an unknown suite")
            expected_keys.add(key)
            expected_counts[suite_id] += 1
        if expected_keys:
            for suite_id, suite in suites.items():
                expected_count = suite.get("expected_query_runs")
                if expected_count is not None and expected_counts[suite_id] != expected_count:
                    raise ValueError("expected query keys do not match a suite's scheduled query count")

    actual_keys: set[tuple[str, str, str]] = set()
    actual_counts: dict[str, int] = defaultdict(int)
    for record in records:
        suite = suites.get(record.suite_id)
        if suites and suite is None:
            raise ValueError("per-query record references an unknown suite")
        if suite is not None and (
            record.system_id != suite.get("system_id")
            or record.budget_id != suite.get("budget_id")
            or record.seed != suite.get("seed")
            or record.ablation_id != suite.get("ablation_id")
        ):
            raise ValueError("per-query record suite identity mismatch")
        key = (record.suite_id, record.case_id, record.cache_state)
        if key in actual_keys:
            raise ValueError("per-query records contain a duplicate query key")
        if expected_keys and key not in expected_keys:
            raise ValueError("per-query record is outside the expected query keys")
        actual_keys.add(key)
        actual_counts[record.suite_id] += 1

    if status == "COMPLETE":
        if raw_expected is None or not expected_keys:
            raise ValueError("complete matrix is missing exact expected query keys")
        if actual_keys != expected_keys:
            raise ValueError("per-query records do not exactly cover the expected query keys")

    raw_statuses = matrix_manifest.get("suite_statuses", [])
    if not isinstance(raw_statuses, list):
        raise TypeError("suite statuses must be a list")
    status_by_suite: dict[str, Mapping[str, Any]] = {}
    for item in raw_statuses:
        if not isinstance(item, Mapping) or not isinstance(item.get("suite_id"), str):
            raise TypeError("matrix contains an invalid suite status")
        suite_id = item["suite_id"]
        if suite_id in status_by_suite:
            raise ValueError("matrix contains duplicate suite statuses")
        if suites and suite_id not in suites:
            raise ValueError("matrix status references an unknown suite")
        if item.get("query_count") != actual_counts[suite_id]:
            raise ValueError("suite query count differs from its per-query records")
        status_by_suite[suite_id] = item

    if status == "COMPLETE":
        expected_total = matrix_manifest.get(
            "expected_query_runs", schedule.get("expected_query_runs")
        )
        if expected_total != len(expected_keys):
            raise ValueError("expected query keys do not match the matrix query count")
        suite_total = matrix_manifest.get(
            "formal_suite_count", schedule.get("formal_suite_count", len(suites))
        )
        if suite_total != len(suites):
            raise ValueError("formal suite count differs from scheduled suite identities")
        if not suites or set(status_by_suite) != set(suites):
            raise ValueError("complete matrix does not contain every scheduled suite status")
        for suite_id, suite in suites.items():
            item = status_by_suite[suite_id]
            if item.get("status") != "COMPLETE":
                raise ValueError("complete matrix contains a non-complete suite")
            if actual_counts[suite_id] != suite.get("expected_query_runs"):
                raise ValueError("per-query records do not match a suite's scheduled query count")
        if any(record.status != "complete" for record in records):
            raise ValueError("complete matrix contains a failed per-query record")
        display_only = (
            matrix_manifest.get("display_only") is True
            or schedule.get("gold_mapping_mode") == "LEGACY_COMPATIBILITY"
        )
        if not display_only and any(
            not record.formal_quality_eligible for record in records
        ):
            raise ValueError("complete formal matrix contains a query without required quality review")


def build_report(
    matrix_manifest: Mapping[str, Any], records: Sequence[QueryRunRecord]
) -> dict[str, Any]:
    schedule = matrix_manifest.get("schedule", matrix_manifest)
    schedule_mode = schedule.get("gold_mapping_mode")
    display_only = schedule_mode == "LEGACY_COMPATIBILITY"
    execution_mode = "LEGACY_DISPLAY_ONLY" if display_only else "FORMAL_G3"
    manifest_mode = matrix_manifest.get("execution_mode")
    if manifest_mode is not None and manifest_mode != execution_mode:
        raise ValueError("matrix execution mode differs from the scheduled Gold mapping mode")
    manifest_display_only = matrix_manifest.get("display_only")
    if manifest_display_only is not None and manifest_display_only is not display_only:
        raise ValueError("matrix display-only flag differs from the scheduled Gold mapping mode")
    if display_only and matrix_manifest.get("formal_quality_eligible") is True:
        raise ValueError("legacy compatibility reports cannot claim formal quality eligibility")
    manifest_generation_id = matrix_manifest.get("generation_id")
    manifest_generation_sha256 = matrix_manifest.get("generation_sha256")
    schedule_generation_id = schedule.get("generation_id")
    schedule_generation_sha256 = schedule.get("generation_sha256")
    if (
        manifest_generation_id is not None
        and schedule_generation_id is not None
        and manifest_generation_id != schedule_generation_id
    ):
        raise ValueError("matrix manifest and schedule generation IDs differ")
    if (
        manifest_generation_sha256 is not None
        and schedule_generation_sha256 is not None
        and manifest_generation_sha256 != schedule_generation_sha256
    ):
        raise ValueError("matrix manifest and schedule generation SHA-256 values differ")
    binding_fields = (
        "dataset_id",
        "dataset_version",
        "dataset_manifest_sha256",
        "dataset_content_sha256",
        "relation_expectations_sha256",
        "required_relation_ids",
        "required_relation_ids_sha256",
        "required_relation_queue_sha256",
        "gold_mapping_mode",
    )
    for field in binding_fields:
        manifest_value = matrix_manifest.get(field)
        schedule_value = schedule.get(field)
        if (
            manifest_value is not None
            and schedule_value is not None
            and manifest_value != schedule_value
        ):
            raise ValueError(f"matrix manifest and schedule {field} values differ")
    generation_id = manifest_generation_id or schedule_generation_id
    generation_sha256 = manifest_generation_sha256 or schedule_generation_sha256
    if records:
        if not generation_id or not generation_sha256:
            raise ValueError("matrix manifest is missing the generation identity for per-query results")
        if any(
            record.generation_id != generation_id
            or record.generation_sha256 != generation_sha256
            for record in records
        ):
            raise ValueError("per-query generation identity does not match the matrix manifest")
    suite_statuses = matrix_manifest.get("suite_statuses", [])
    blocked_count = sum(
        item.get("status") == "BLOCKED"
        for item in schedule.get("suites", [])
    )
    failed_count = sum(item.get("status") == "FAILED" for item in suite_statuses)
    complete_count = sum(item.get("status") == "COMPLETE" for item in suite_statuses)
    status = matrix_manifest.get("status", schedule.get("status", "BLOCKED"))
    if not isinstance(status, str):
        raise TypeError("matrix status must be a string")
    _validate_query_coverage(matrix_manifest, schedule, records, status=status)
    report_records = (
        [record.model_copy(update={"formal_quality_eligible": False}) for record in records]
        if display_only
        else records
    )
    summary = summarize_query_runs(report_records)
    report = {
        "schema_version": 3,
        "status": status,
        "execution_mode": execution_mode,
        "display_only": display_only,
        "formal_quality_eligible": (
            not display_only
            and bool(records)
            and all(record.formal_quality_eligible for record in records)
            and status == "COMPLETE"
        ),
        "generation_id": generation_id,
        "generation_sha256": generation_sha256,
        **{
            field: (
                matrix_manifest.get(field)
                if matrix_manifest.get(field) is not None
                else schedule.get(field)
            )
            for field in binding_fields
        },
        "protocol_id": schedule.get("protocol_id"),
        "formal_suite_count": schedule.get("formal_suite_count", len(schedule.get("suites", []))),
        "blocked_suite_count": blocked_count,
        "failed_suite_count": failed_count,
        "complete_suite_count": complete_count,
        "expected_query_runs": matrix_manifest.get("expected_query_runs", schedule.get("expected_query_runs")),
        "expected_quality_query_runs": (
            0
            if display_only
            else matrix_manifest.get(
                "expected_quality_query_runs", schedule.get("expected_quality_query_runs")
            )
        ),
        "query_record_count": len(records),
        "blockers": matrix_manifest.get("blockers", schedule.get("blockers", [])),
        "missing_runs": matrix_manifest.get("missing_runs", schedule.get("missing_runs", [])),
        **summary,
        "standard_paired_comparisons": (
            [] if display_only else paired_system_comparisons(report_records)
        ),
        "standard_quality_cost_pareto": (
            [] if display_only else system_pareto_frontier(report_records)
        ),
        "policy_training_summary": matrix_manifest.get("policy_training_summary"),
    }
    return report


def render_markdown_report(report: Mapping[str, Any]) -> str:
    display_only = report.get("display_only") is True or report.get(
        "execution_mode"
    ) == "LEGACY_DISPLAY_ONLY"
    lines = [
        "# B07.final compatibility display" if display_only else "# B07.final experiment report",
        "",
        f"- Mode: **{'DISPLAY_ONLY' if display_only else 'FORMAL_G3'}**",
        f"- Status: **{report.get('status', 'UNKNOWN')}**",
        (
            f"- {'Execution' if display_only else 'Formal'} suites: {report.get('complete_suite_count', 0)}/"
            f"{report.get('formal_suite_count', 111)} complete; "
            f"{report.get('blocked_suite_count', 0)} blocked; {report.get('failed_suite_count', 0)} failed"
        ),
        (
            f"- {'Formal quality cases' if display_only else 'Quality cases'}: "
            f"{report.get('expected_quality_query_runs', 'unknown')}; "
            f"query executions across cache states: {report.get('query_record_count', 0)} "
            f"(expected {report.get('expected_query_runs', 'unknown')})"
        ),
        "",
    ]
    blockers = report.get("blockers", [])
    if blockers:
        lines.extend(["## Blockers", "", *[f"- {item}" for item in blockers], ""])
    training = report.get("policy_training_summary")
    if training:
        lines.extend(
            [
                "## Training and dev-selection cost",
                "",
                "| Stage | Tokens | USD | CNY | Elapsed ms | Count kind |",
                "|---|---:|---:|---:|---:|---|",
                "| Train action exploration | {tokens} | {usd} | {cny} | {elapsed} | {kind} |".format(
                    tokens=training.get("training_tokens"),
                    usd=training.get("training_cost_usd"),
                    cny=training.get("training_cost_cny"),
                    elapsed=training.get("training_elapsed_ms"),
                    kind=training.get("training_count_kind"),
                ),
                "| Dev parameter selection | {tokens} | {usd} | {cny} | {elapsed} | {kind} |".format(
                    tokens=training.get("dev_tokens"),
                    usd=training.get("dev_cost_usd"),
                    cny=training.get("dev_cost_cny"),
                    elapsed=training.get("dev_elapsed_ms"),
                    kind=training.get("dev_count_kind"),
                ),
                "",
            ]
        )
    summaries = report.get("suite_summaries", [])
    if summaries:
        if display_only:
            lines.extend(
                [
                    "## Suite usage (display only)",
                    "",
                    "| Suite and cache | Queries | Failures | Tokens | USD | CNY | Latency p50/p95 ms |",
                    "|---|---:|---:|---:|---:|---:|---:|",
                ]
            )
            for item in summaries:
                lines.append(
                    "| {suite} ({cache}) | {count} | {failed} | {tokens} | {usd} | {cny} | {p50}/{p95} |".format(
                        suite=item["suite_id"],
                        cache=item["cache_state"],
                        count=item["query_count"],
                        failed=item["failed_query_count"],
                        tokens=item["total_tokens"],
                        usd=item["currency_cost_usd"],
                        cny=item["currency_cost_cny"],
                        p50=item["end_to_end_latency_p50_ms"],
                        p95=item["end_to_end_latency_p95_ms"],
                    )
                )
        else:
            lines.extend(
                [
                    "## Suite metrics and usage",
                    "",
                    "| Suite and cache | Queries | Failures | Evidence recall | Answer correctness (0–2) | Tokens | USD | CNY | Latency p50/p95 ms |",
                    "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
                ]
            )
            for item in summaries:
                lines.append(
                    "| {suite} ({cache}) | {count} | {failed} | {recall} | {correct} | {tokens} | {usd} | {cny} | {p50}/{p95} |".format(
                        suite=item["suite_id"],
                        cache=item["cache_state"],
                        count=item["query_count"],
                        failed=item["failed_query_count"],
                        recall=item["mean_evidence_recall"],
                        correct=item["answer_correctness_mean_0_to_2"],
                        tokens=item["total_tokens"],
                        usd=item["currency_cost_usd"],
                        cny=item["currency_cost_cny"],
                        p50=item["end_to_end_latency_p50_ms"],
                        p95=item["end_to_end_latency_p95_ms"],
                    )
                )
        lines.append("")
    comparisons = [] if display_only else report.get("standard_paired_comparisons", [])
    if comparisons:
        lines.extend(["## Paired source-family comparisons", ""])
        for item in comparisons:
            lines.append(
                f"- {item['challenger']} vs {item['baseline']} ({item['metric']}): "
                f"difference={item['mean_difference']:.4f}, "
                f"95% CI [{item['lower']:.4f}, {item['upper']:.4f}], "
                f"clusters={item['family_cluster_count']}, "
                f"confirmatory={item['confirmatory_family_count']}"
            )
        lines.append("")
    frontier = [] if display_only else report.get("standard_quality_cost_pareto", [])
    if frontier:
        lines.extend(["## Standard-budget quality/cost Pareto frontier", ""])
        for item in frontier:
            lines.append(
                f"- {item['system_id']}: quality={item['quality']:.4f}, "
                f"tokens={item['total_tokens']}, latency_ms={item['latency_ms']}, "
                f"USD={item['currency_cost']}"
            )
        lines.append("")
    missing = report.get("missing_runs", [])
    if missing:
        lines.extend(["## Missing or blocked suites", "", *[f"- {item}" for item in missing], ""])
    return "\n".join(lines)


def recompute_report_files(
    matrix_manifest_path: Path, per_query_path: Path, report_path: Path
) -> dict[str, Any]:
    matrix = json.loads(matrix_manifest_path.read_text(encoding="utf-8"))
    if not isinstance(matrix, Mapping):
        raise TypeError("matrix manifest must be an object")
    expected_jsonl_sha256 = matrix.get("per_query_jsonl_sha256")
    jsonl_bytes = per_query_path.read_bytes()
    actual_jsonl_sha256 = hashlib.sha256(jsonl_bytes).hexdigest()
    if (
        not isinstance(expected_jsonl_sha256, str)
        or len(expected_jsonl_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_jsonl_sha256)
        or expected_jsonl_sha256 != actual_jsonl_sha256
    ):
        raise ValueError("per-query JSONL SHA-256 does not match the matrix manifest")
    records: list[QueryRunRecord] = []
    for line_number, line in enumerate(jsonl_bytes.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            raise ValueError(f"blank per-query JSONL row at line {line_number}")
        records.append(QueryRunRecord.model_validate_json(line))
    report = build_report(matrix, records)
    markdown = render_markdown_report(report)
    report_path.write_text(markdown, encoding="utf-8", newline="\n")
    return report
