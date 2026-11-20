"""Source-family paired uncertainty and quality/cost frontier helpers."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class PairedBootstrapCI:
    mean_difference: float
    lower: float
    upper: float
    confidence_level: float
    resamples: int
    case_count: int
    family_cluster_count: int
    confirmatory_family_count: bool


@dataclass(frozen=True)
class ParetoPoint:
    system_id: str
    quality: float
    total_tokens: int
    latency_ms: int
    currency_cost: float | None = None


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("percentile requires at least one sample")
    position = (len(sorted_values) - 1) * probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return sorted_values[lower_index]
    fraction = position - lower_index
    return sorted_values[lower_index] * (1.0 - fraction) + sorted_values[upper_index] * fraction


def _family_components(
    families_by_case: Mapping[str, Sequence[str]],
) -> dict[str, str]:
    parent: dict[str, str] = {}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        if parent[item] != item:
            parent[item] = find(parent[item])
        return parent[item]

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for families in families_by_case.values():
        unique = sorted(set(families))
        if not unique:
            raise ValueError("every evaluated case must identify at least one source family")
        for family in unique:
            find(family)
        for family in unique[1:]:
            union(unique[0], family)

    return {family: find(family) for family in tuple(parent)}


def paired_source_family_bootstrap(
    left: Mapping[str, float],
    right: Mapping[str, float],
    families_by_case: Mapping[str, Sequence[str]],
    *,
    resamples: int = 10_000,
    seed: int = 17,
    confidence_level: float = 0.95,
) -> PairedBootstrapCI:
    """Bootstrap paired system differences by connected source-family clusters.

    Cases that cite multiple families connect those families into one resampling
    cluster so a cross-source case is never duplicated or split across clusters.
    """
    case_ids = set(left)
    if case_ids != set(right) or case_ids != set(families_by_case):
        raise ValueError("paired systems and family map must contain identical case IDs")
    if not case_ids:
        raise ValueError("paired bootstrap requires at least one case")
    if resamples < 1:
        raise ValueError("resamples must be positive")
    if confidence_level != 0.95:
        raise ValueError("the frozen evaluation plan requires 95% confidence intervals")
    if any(not math.isfinite(value) for value in (*left.values(), *right.values())):
        raise ValueError("paired metric values must be finite")

    components = _family_components(families_by_case)
    cluster_differences: dict[str, list[float]] = defaultdict(list)
    differences: dict[str, float] = {}
    for case_id in sorted(case_ids):
        family_ids = sorted(set(families_by_case[case_id]))
        cluster_roots = {components[family] for family in family_ids}
        if len(cluster_roots) != 1:
            raise ValueError("multi-source case families were not joined into one cluster")
        difference = float(left[case_id]) - float(right[case_id])
        differences[case_id] = difference
        cluster_differences[next(iter(cluster_roots))].append(difference)

    cluster_ids = sorted(cluster_differences)
    point_estimate = sum(differences.values()) / len(differences)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(resamples):
        selected_clusters = [rng.choice(cluster_ids) for _ in cluster_ids]
        sampled = [
            difference
            for cluster_id in selected_clusters
            for difference in cluster_differences[cluster_id]
        ]
        samples.append(sum(sampled) / len(sampled))
    samples.sort()
    tail = (1.0 - confidence_level) / 2.0
    return PairedBootstrapCI(
        mean_difference=point_estimate,
        lower=_percentile(samples, tail),
        upper=_percentile(samples, 1.0 - tail),
        confidence_level=confidence_level,
        resamples=resamples,
        case_count=len(case_ids),
        family_cluster_count=len(cluster_ids),
        confirmatory_family_count=len(cluster_ids) >= 5,
    )


def pareto_frontier(points: Sequence[ParetoPoint]) -> tuple[ParetoPoint, ...]:
    """Return systems not dominated on quality, token spend, and end-to-end latency."""
    if any(
        not point.system_id
        or not math.isfinite(point.quality)
        or not 0.0 <= point.quality <= 1.0
        or point.total_tokens < 0
        or point.latency_ms < 0
        or (
            point.currency_cost is not None
            and (not math.isfinite(point.currency_cost) or point.currency_cost < 0.0)
        )
        for point in points
    ):
        raise ValueError("Pareto points contain invalid quality or cost values")

    frontier: list[ParetoPoint] = []
    for candidate in points:
        dominated = any(
            other is not candidate
            and other.quality >= candidate.quality
            and other.total_tokens <= candidate.total_tokens
            and other.latency_ms <= candidate.latency_ms
            and (
                candidate.currency_cost is None
                or other.currency_cost is None
                or other.currency_cost <= candidate.currency_cost
            )
            and (
                other.quality > candidate.quality
                or other.total_tokens < candidate.total_tokens
                or other.latency_ms < candidate.latency_ms
                or (
                    candidate.currency_cost is not None
                    and other.currency_cost is not None
                    and other.currency_cost < candidate.currency_cost
                )
            )
            for other in points
        )
        if not dominated:
            frontier.append(candidate)
    return tuple(sorted(frontier, key=lambda item: (item.total_tokens, item.latency_ms, -item.quality, item.system_id)))
