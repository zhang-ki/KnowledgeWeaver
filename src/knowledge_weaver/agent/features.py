from __future__ import annotations

import math
import re
from collections.abc import Mapping

from knowledge_weaver.models import Action, ActionTool, InvestigationState, SourceKind

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+")
_EXACT_SYMBOL = re.compile(
    r"`([^`]{1,100})`|\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\b|"
    r"\b([A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+)\b|"
    r"\b([A-Z][a-z0-9]*[A-Z][A-Za-z0-9_]*)\b|\b([A-Z_]{3,})\b|"
    r"\b([A-Za-z_][A-Za-z0-9_]*\(\))"
)


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    if not math.isfinite(value):
        return lower
    return min(upper, max(lower, value))


def _support_rows(state: InvestigationState) -> list[Mapping[str, object]]:
    raw = state.policy_memory.get("support_rows", [])
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, Mapping)]


def extract_exact_symbol(query: str) -> str | None:
    match = _EXACT_SYMBOL.search(query)
    if not match:
        return None
    return next((group for group in match.groups() if group), match.group(0))


def facet_coverage(state: InvestigationState, facet_id: str) -> float:
    grouped: dict[str, float] = {}
    for row in _support_rows(state):
        if row.get("facet_id") != facet_id:
            continue
        evidence_id = str(row.get("evidence_id", ""))
        duplicate_key = str(row.get("duplicate_key") or evidence_id)
        support = row.get("support", 0.0)
        if isinstance(support, (int, float)):
            grouped[duplicate_key] = max(grouped.get(duplicate_key, 0.0), float(support))
    return max(grouped.values(), default=0.0)


def support_coverage(state: InvestigationState) -> float:
    if not state.subquestions:
        return 0.0
    return sum(facet_coverage(state, facet.id) for facet in state.subquestions) / len(state.subquestions)


def unresolved_ratio(state: InvestigationState) -> float:
    if not state.subquestions:
        return 1.0
    unresolved = sum(facet_coverage(state, facet.id) < 0.8 for facet in state.subquestions)
    return unresolved / len(state.subquestions)


def conflict_ratio(state: InvestigationState) -> float:
    if not state.subquestions:
        return 0.0
    conflicted = {
        str(row.get("facet_id"))
        for row in _support_rows(state)
        if row.get("conflict") is True
    }
    return len(conflicted) / len(state.subquestions)


def independent_source_gap(state: InvestigationState, facet_id: str) -> float:
    facet = next((item for item in state.subquestions if item.id == facet_id), None)
    required = {item.value for item in facet.required_source_types} if facet else set()
    if not required:
        return 0.0
    found: set[str] = set()
    for row in _support_rows(state):
        support = row.get("support")
        if (
            row.get("facet_id") == facet_id
            and isinstance(support, (int, float))
            and float(support) > 0.0
            and isinstance(row.get("source_kind"), str)
        ):
            found.add(str(row["source_kind"]))
    return clamp(len(required - found) / len(required))


def _exact_symbol(query: str) -> bool:
    return extract_exact_symbol(query) is not None


def _source_match(state: InvestigationState, action: Action, facet_id: str) -> float:
    facet = next((item for item in state.subquestions if item.id == facet_id), None)
    required = {item.value for item in facet.required_source_types} if facet else set()
    query = facet.text if facet else state.query.text
    exact_symbol = _exact_symbol(query)
    has_seed = bool(state.policy_memory.get("seed_available")) or bool(state.paths)

    if action.tool in {ActionTool.bm25, ActionTool.dense}:
        return 1.0 if required else 0.5
    if action.tool is ActionTool.symbol:
        python_required = SourceKind.python_repo.value in required
        return 1.0 if python_required or exact_symbol else 0.2
    if action.tool is ActionTool.entity:
        return 0.85 if exact_symbol or required else 0.4
    if action.tool is ActionTool.graph:
        return 1.0 if has_seed or len(required) > 1 else 0.25
    if action.tool is ActionTool.rerank:
        return 0.65 if state.evidence_ledger else 0.0
    return 0.0


def _recent_gain(state: InvestigationState, family: str) -> float:
    raw = state.policy_memory.get("gain_ema", {})
    value = raw.get(family, 0.0) if isinstance(raw, Mapping) else 0.0
    return clamp(float(value), -1.0, 1.0) if isinstance(value, (int, float)) else 0.0


def _action_family_count(state: InvestigationState, family: str) -> float:
    counts = state.policy_memory.get("family_counts", {})
    count = int(counts.get(family, 0)) if isinstance(counts, Mapping) else 0
    max_steps = int(state.policy_memory.get("initial_max_steps", state.remaining_budget.max_steps))
    return clamp(count / max(1, max_steps))


def remaining_budget_ratio(state: InvestigationState) -> float:
    provided = state.policy_memory.get("remaining_budget_ratio")
    if isinstance(provided, (int, float)):
        return clamp(float(provided))
    return 1.0


def feature_vector(
    state: InvestigationState,
    action: Action,
    *,
    enable_source_features: bool = True,
) -> list[float]:
    facet_id = action.facet_id or (state.subquestions[0].id if state.subquestions else "")
    facet = next((item for item in state.subquestions if item.id == facet_id), None)
    query = facet.text if facet else state.query.text
    tokens = _TOKEN.findall(query.casefold())
    lexical_specificity = clamp(len(set(tokens)) / 20.0)
    symbol_flag = 1.0 if _exact_symbol(query) else 0.0
    seed_available = 1.0 if state.policy_memory.get("seed_available") or state.paths else 0.0

    return [
        1.0,
        unresolved_ratio(state),
        _source_match(state, action, facet_id) if enable_source_features else 0.0,
        clamp(support_coverage(state)),
        conflict_ratio(state),
        independent_source_gap(state, facet_id) if enable_source_features else 0.0,
        clamp((_recent_gain(state, action.tool.value) + 1.0) / 2.0),
        lexical_specificity,
        symbol_flag,
        seed_available,
        _action_family_count(state, action.tool.value),
        remaining_budget_ratio(state),
    ]


def duplicate_risk(state: InvestigationState, action: Action) -> float:
    attempts = state.policy_memory.get("attempts", [])
    if not isinstance(attempts, list):
        return 0.0
    same_facet = [
        item
        for item in attempts
        if isinstance(item, Mapping)
        and item.get("tool") == action.tool.value
        and item.get("facet_id") == action.facet_id
    ]
    if not same_facet:
        return 0.0
    if any(item.get("new_evidence_ids") for item in same_facet if isinstance(item, Mapping)):
        return 0.5
    return 1.0


def stable_action_key(action: Action) -> tuple[object, ...]:
    return (
        action.tool.value,
        action.facet_id or "",
        action.query,
        action.top_k,
        action.hops or 0,
        action.rerank,
        action.id,
    )
