"""Immutable identities for frozen B07 suites and their runtime routes."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from knowledge_weaver.evaluation.protocol import (
    AblationSpec,
    BudgetSpec,
    EvaluationProtocol,
    SystemSpec,
)
from knowledge_weaver.evaluation.scheduling import ExperimentSuite, MatrixSchedule

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

SYSTEM_ROUTES: Mapping[str, str] = MappingProxyType(
    {
        "bm25": "retriever.bm25",
        "dense": "retriever.dense",
        "hybrid_rrf": "retriever.hybrid_rrf",
        "hybrid_rerank": "retriever.hybrid_rerank",
        "graph": "retriever.graph",
        "fixed_agent": "agent.fixed",
        "rule": "agent.rule",
        "llm_router": "agent.llm_router",
        "segr": "agent.segr",
    }
)


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _route_for_suite(suite: ExperimentSuite) -> str:
    if suite.ablation_id is not None:
        return f"agent.segr.ablation.{suite.ablation_id}"
    try:
        return SYSTEM_ROUTES[suite.system_id]
    except KeyError as exc:
        raise ValueError(f"unknown frozen B07 system: {suite.system_id}") from exc


@dataclass(frozen=True, slots=True)
class RouteBinding:
    route_id: str
    implementation_id: str
    config_sha256: str
    adapter: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.route_id or self.implementation_id != self.route_id:
            raise ValueError("route binding implementation identity must match its route ID")
        if not SHA256_PATTERN.fullmatch(self.config_sha256):
            raise ValueError("route binding requires a lowercase SHA-256 config digest")


@dataclass(frozen=True, slots=True)
class SuiteExecutionPlan:
    suite_id: str
    scope: Literal["formal", "dev_vertical_slice"]
    system_id: str
    ablation_id: str | None
    budget_id: str
    seed: int
    dataset_content_sha256: str
    route_id: str
    route_config_sha256: str
    generation_config_sha256: str | None
    weights_sha256: str | None
    execution_config_sha256: str
    executable: bool
    blockers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "scope": self.scope,
            "system_id": self.system_id,
            "ablation_id": self.ablation_id,
            "budget_id": self.budget_id,
            "seed": self.seed,
            "dataset_content_sha256": self.dataset_content_sha256,
            "route_id": self.route_id,
            "route_config_sha256": self.route_config_sha256,
            "generation_config_sha256": self.generation_config_sha256,
            "weights_sha256": self.weights_sha256,
            "execution_config_sha256": self.execution_config_sha256,
            "executable": self.executable,
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True, slots=True)
class SuiteExecutionRegistry:
    protocol_id: str
    protocol_sha256: str
    plans: Mapping[str, SuiteExecutionPlan]
    route_bindings: Mapping[str, RouteBinding]
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "plans", MappingProxyType(dict(self.plans)))
        object.__setattr__(self, "route_bindings", MappingProxyType(dict(self.route_bindings)))
        if any(key != plan.suite_id for key, plan in self.plans.items()):
            raise ValueError("suite registry key differs from its immutable suite ID")
        config_hashes = [plan.execution_config_sha256 for plan in self.plans.values()]
        if len(config_hashes) != len(set(config_hashes)):
            raise ValueError("each suite must have a unique execution config digest")
        expected_sha256 = _canonical_sha256(
            {
                "protocol_id": self.protocol_id,
                "protocol_sha256": self.protocol_sha256,
                "plans": [self.plans[key].to_dict() for key in sorted(self.plans)],
            }
        )
        if self.sha256 != expected_sha256:
            raise ValueError("suite registry SHA does not match its immutable plan set")

    def plan_for(self, suite_id: str) -> SuiteExecutionPlan:
        try:
            return self.plans[suite_id]
        except KeyError as exc:
            raise ValueError(f"suite is not present in the frozen registry: {suite_id}") from exc

    def binding_for(self, route_id: str) -> RouteBinding | None:
        return self.route_bindings.get(route_id)


def build_suite_registry(
    schedule: MatrixSchedule,
    protocol: EvaluationProtocol,
    *,
    route_bindings: Mapping[str, RouteBinding] | None = None,
    generation_config_sha256: str | None = None,
    dev_dataset_sha256: str | None = None,
) -> SuiteExecutionRegistry:
    """Freeze the complete 111-suite identity map without claiming missing routes work."""
    bindings = dict(route_bindings or {})
    protocol_sha256 = _canonical_sha256(protocol.model_dump(mode="json"))
    if schedule.protocol_id != protocol.protocol_id or schedule.protocol_sha256 != protocol_sha256:
        raise ValueError("schedule protocol identity differs from the frozen protocol")
    for digest, label in (
        (generation_config_sha256, "generation config"),
        (dev_dataset_sha256, "dev dataset"),
    ):
        if digest is not None and not SHA256_PATTERN.fullmatch(digest):
            raise ValueError(f"{label} identity must be a lowercase SHA-256 digest")
    for key, binding in bindings.items():
        if key != binding.route_id:
            raise ValueError("route binding key does not match its immutable route ID")
    adapter_routes: dict[int, str] = {}
    implementation_ids: dict[str, str] = {}
    for binding in bindings.values():
        instance_key = id(binding.adapter)
        previous_route = adapter_routes.get(instance_key)
        if previous_route is not None and previous_route != binding.route_id:
            raise ValueError("one adapter instance cannot impersonate multiple suite routes")
        adapter_routes[instance_key] = binding.route_id
        previous_route = implementation_ids.get(binding.implementation_id)
        if previous_route is not None and previous_route != binding.route_id:
            raise ValueError("implementation identity cannot be reused for another suite route")
        implementation_ids[binding.implementation_id] = binding.route_id

    system_specs: dict[str, SystemSpec] = {item.id: item for item in protocol.systems}
    budget_specs: dict[str, BudgetSpec] = {item.id: item for item in protocol.budgets}
    ablation_specs: dict[str, AblationSpec] = {item.id: item for item in protocol.ablations}
    expected_ids = {
        f"system/{system.id}/{budget.id}/seed-{seed}"
        for system in protocol.systems
        for budget in protocol.budgets
        for seed in protocol.seeds
    } | {
        f"ablation/{ablation.id}/standard/seed-{seed}"
        for ablation in protocol.ablations
        for seed in protocol.seeds
    }
    actual_ids = {suite.suite_id for suite in schedule.suites}
    if len(schedule.suites) != protocol.formal_suite_count or actual_ids != expected_ids:
        raise ValueError("schedule does not contain the exact frozen formal suite set")

    plans: dict[str, SuiteExecutionPlan] = {}
    for suite in schedule.suites:
        route_id = _route_for_suite(suite)
        route_binding = bindings.get(route_id)
        if suite.ablation_id is None:
            system = system_specs[suite.system_id]
            behavior: object = system.behavior
        else:
            try:
                behavior = ablation_specs[suite.ablation_id].behavior
            except KeyError as exc:
                raise ValueError("suite references an unknown frozen ablation") from exc
        budget = budget_specs.get(suite.budget_id)
        if budget is None:
            raise ValueError("suite references an unknown frozen budget")
        route_config_sha256 = (
            route_binding.config_sha256
            if route_binding is not None
            else _canonical_sha256(
                {
                    "protocol_sha256": protocol_sha256,
                    "route_id": route_id,
                    "frozen_behavior": behavior,
                }
            )
        )
        weights_sha256 = (
            schedule.policy_artifact_sha256
            if suite.system_id == "segr" or suite.ablation_id is not None
            else None
        )
        blockers: list[str] = []
        blockers.append(
            "BLOCKED_CACHE_CONTROL_PORT: suite registry has no frozen B04 cache-control profile"
        )
        if route_id.startswith("agent."):
            blockers.append("BLOCKED_B06_TRACE_PORT: AgentService does not expose ordered retrieval trace")
        elif route_binding is None:
            blockers.append(f"BLOCKED_ROUTE_BINDING: {route_id} has no runtime adapter")
        if generation_config_sha256 is None:
            blockers.append("BLOCKED_GENERATION_CONFIG: shared answer provider config SHA is missing")
        if (suite.system_id == "segr" or suite.ablation_id is not None) and weights_sha256 is None:
            blockers.append("BLOCKED_POLICY: frozen policy weights SHA is missing")
        config_payload = {
            "suite_id": suite.suite_id,
            "scope": "formal",
            "protocol_sha256": protocol_sha256,
            "system_id": suite.system_id,
            "system_behavior": (
                system_specs[suite.system_id].behavior if suite.ablation_id is None else None
            ),
            "ablation_id": suite.ablation_id,
            "ablation_behavior": behavior if suite.ablation_id is not None else None,
            "budget": budget.model_dump(mode="json"),
            "seed": suite.seed,
            "dataset_content_sha256": schedule.dataset_content_sha256,
            "route_id": route_id,
            "route_config_sha256": route_config_sha256,
            "generation_config_sha256": generation_config_sha256,
            "weights_sha256": weights_sha256,
        }
        plans[suite.suite_id] = SuiteExecutionPlan(
            suite_id=suite.suite_id,
            scope="formal",
            system_id=suite.system_id,
            ablation_id=suite.ablation_id,
            budget_id=suite.budget_id,
            seed=suite.seed,
            dataset_content_sha256=schedule.dataset_content_sha256,
            route_id=route_id,
            route_config_sha256=route_config_sha256,
            generation_config_sha256=generation_config_sha256,
            weights_sha256=weights_sha256,
            execution_config_sha256=_canonical_sha256(config_payload),
            executable=not blockers,
            blockers=tuple(blockers),
        )

    if dev_dataset_sha256 is not None:
        dev_suite_id = "dev/dense/standard/seed-17"
        dense_suite = next(
            item for item in schedule.suites if item.suite_id == "system/dense/standard/seed-17"
        )
        route_id = "retriever.dense"
        dev_route_binding = bindings.get(route_id)
        blockers = []
        if dev_route_binding is None:
            blockers.append("BLOCKED_ROUTE_BINDING: retriever.dense has no runtime adapter")
        if generation_config_sha256 is None:
            blockers.append("BLOCKED_GENERATION_CONFIG: shared answer provider config SHA is missing")
        route_config_sha256 = dev_route_binding.config_sha256 if dev_route_binding else _canonical_sha256(
            {"route_id": route_id, "frozen_behavior": system_specs["dense"].behavior}
        )
        budget = budget_specs["standard"]
        config_payload = {
            "suite_id": dev_suite_id,
            "scope": "dev_vertical_slice",
            "protocol_sha256": protocol_sha256,
            "dataset_sha256": dev_dataset_sha256,
            "system_id": "dense",
            "system_behavior": system_specs["dense"].behavior,
            "budget": budget.model_dump(mode="json"),
            "seed": 17,
            "cache_states": ["cold"],
            "top_k": 20,
            "route_id": route_id,
            "route_config_sha256": route_config_sha256,
            "generation_config_sha256": generation_config_sha256,
        }
        plans[dev_suite_id] = SuiteExecutionPlan(
            suite_id=dev_suite_id,
            scope="dev_vertical_slice",
            system_id="dense",
            ablation_id=None,
            budget_id="standard",
            seed=17,
            dataset_content_sha256=dev_dataset_sha256,
            route_id=route_id,
            route_config_sha256=route_config_sha256,
            generation_config_sha256=generation_config_sha256,
            weights_sha256=None,
            execution_config_sha256=_canonical_sha256(config_payload),
            executable=not blockers and dense_suite.budget_id == "standard",
            blockers=tuple(blockers),
        )

    registry_sha256 = _canonical_sha256(
        {
            "protocol_id": protocol.protocol_id,
            "protocol_sha256": protocol_sha256,
            "plans": [plans[key].to_dict() for key in sorted(plans)],
        }
    )
    return SuiteExecutionRegistry(
        protocol_id=protocol.protocol_id,
        protocol_sha256=protocol_sha256,
        plans=plans,
        route_bindings=bindings,
        sha256=registry_sha256,
    )
