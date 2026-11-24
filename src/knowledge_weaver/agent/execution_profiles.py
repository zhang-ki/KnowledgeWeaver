"""Allowlisted B07-to-B06 Agent behavior profiles and stable config identities."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from knowledge_weaver.models import PolicyKind


@dataclass(frozen=True, slots=True)
class AgentBehaviorProfile:
    route_id: str
    system_id: str
    ablation_id: str | None
    policy: PolicyKind
    feature_overrides: tuple[tuple[str, bool | float], ...]
    supported: bool = True
    unsupported_reason: str | None = None

    @property
    def config_sha256(self) -> str:
        payload = {
            "contract": "b06-agent-execution-profile-v1",
            "route_id": self.route_id,
            "system_id": self.system_id,
            "ablation_id": self.ablation_id,
            "policy": self.policy.value,
            "feature_overrides": dict(self.feature_overrides),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def request_features(self) -> dict[str, Any]:
        return dict(self.feature_overrides)


_profiles = {
    "agent.fixed": AgentBehaviorProfile(
        route_id="agent.fixed",
        system_id="fixed_agent",
        ablation_id=None,
        policy=PolicyKind.fixed,
        feature_overrides=(),
        supported=False,
        unsupported_reason=(
            "the frozen fixed_agent contract requires fixed hybrid, graph, expansion, and rerank; "
            "B06 FixedPolicy currently orders individual retrieval tools"
        ),
    ),
    "agent.rule": AgentBehaviorProfile(
        route_id="agent.rule",
        system_id="rule",
        ablation_id=None,
        policy=PolicyKind.rule,
        feature_overrides=(),
    ),
    "agent.llm_router": AgentBehaviorProfile(
        route_id="agent.llm_router",
        system_id="llm_router",
        ablation_id=None,
        policy=PolicyKind.llm,
        feature_overrides=(),
    ),
    "agent.segr": AgentBehaviorProfile(
        route_id="agent.segr",
        system_id="segr",
        ablation_id=None,
        policy=PolicyKind.segr,
        feature_overrides=(),
    ),
    "agent.segr.ablation.A01": AgentBehaviorProfile(
        route_id="agent.segr.ablation.A01",
        system_id="segr",
        ablation_id="A01",
        policy=PolicyKind.fixed,
        feature_overrides=(("enable_dynamic_stop", False),),
    ),
    "agent.segr.ablation.A02": AgentBehaviorProfile(
        route_id="agent.segr.ablation.A02",
        system_id="segr",
        ablation_id="A02",
        policy=PolicyKind.segr,
        feature_overrides=(("enable_graph", False),),
    ),
    "agent.segr.ablation.A03": AgentBehaviorProfile(
        route_id="agent.segr.ablation.A03",
        system_id="segr",
        ablation_id="A03",
        policy=PolicyKind.segr,
        feature_overrides=(("enable_relation_semantic_verification", False),),
    ),
    "agent.segr.ablation.A04": AgentBehaviorProfile(
        route_id="agent.segr.ablation.A04",
        system_id="segr",
        ablation_id="A04",
        policy=PolicyKind.segr,
        feature_overrides=(("enable_expansion", False),),
    ),
    "agent.segr.ablation.A05": AgentBehaviorProfile(
        route_id="agent.segr.ablation.A05",
        system_id="segr",
        ablation_id="A05",
        policy=PolicyKind.segr,
        feature_overrides=(("enable_reranker", False),),
    ),
    "agent.segr.ablation.A06": AgentBehaviorProfile(
        route_id="agent.segr.ablation.A06",
        system_id="segr",
        ablation_id="A06",
        policy=PolicyKind.segr,
        feature_overrides=(("enable_sufficiency_stop", False),),
    ),
    "agent.segr.ablation.A07": AgentBehaviorProfile(
        route_id="agent.segr.ablation.A07",
        system_id="segr",
        ablation_id="A07",
        policy=PolicyKind.segr,
        feature_overrides=(("enable_dynamic_stop", False), ("enable_sufficiency_stop", False)),
    ),
    "agent.segr.ablation.A08": AgentBehaviorProfile(
        route_id="agent.segr.ablation.A08",
        system_id="segr",
        ablation_id="A08",
        policy=PolicyKind.segr,
        feature_overrides=(("enable_source_features", False),),
    ),
    "agent.segr.ablation.A09": AgentBehaviorProfile(
        route_id="agent.segr.ablation.A09",
        system_id="segr",
        ablation_id="A09",
        policy=PolicyKind.segr,
        feature_overrides=(("enable_learned_gain", False),),
    ),
    "agent.segr.ablation.A10": AgentBehaviorProfile(
        route_id="agent.segr.ablation.A10",
        system_id="segr",
        ablation_id="A10",
        policy=PolicyKind.segr,
        feature_overrides=(("lambda_cost", 0.0),),
    ),
}

AGENT_BEHAVIOR_PROFILES = MappingProxyType(_profiles)
SUPPORTED_AGENT_ROUTE_IDS = tuple(
    route_id for route_id, profile in _profiles.items() if profile.supported
)


def agent_behavior_profile(route_id: str, *, require_supported: bool = True) -> AgentBehaviorProfile:
    try:
        profile = AGENT_BEHAVIOR_PROFILES[route_id]
    except KeyError as exc:
        raise ValueError(f"unknown frozen B07 Agent route: {route_id}") from exc
    if require_supported and not profile.supported:
        raise ValueError(profile.unsupported_reason or f"Agent route is unsupported: {route_id}")
    return profile
