from pathlib import Path
from types import SimpleNamespace

import pytest
from test_generation_binding import _make_generation

from knowledge_weaver.evaluation.dataset import load_dataset
from knowledge_weaver.evaluation.protocol import load_protocol
from knowledge_weaver.evaluation.runner import _registered_suite_blocker
from knowledge_weaver.evaluation.scheduling import (
    REQUIRED_DEPENDENCIES,
    build_formal_matrix,
)
from knowledge_weaver.evaluation.suite_registry import (
    SYSTEM_ROUTES,
    RouteBinding,
    build_suite_registry,
)


def _registry_inputs():
    root = Path(__file__).resolve().parents[2]
    protocol = load_protocol(root / "benchmarks/configs/b07_protocol_v1.json")
    dataset = load_dataset(root)
    generation, generation_sha256 = _make_generation()
    schedule = build_formal_matrix(
        protocol,
        dataset,
        dependency_commits=REQUIRED_DEPENDENCIES,
        dependencies_integrated=True,
        provider_profiles=("fixture",),
        provider_ready=True,
        budget_approved=True,
        policy_artifact_ready=True,
        policy_artifact_sha256="f" * 64,
        generation_id=generation.id,
        generation_sha256=generation_sha256,
    )
    return protocol, dataset, schedule


def test_registry_freezes_all_111_suites_and_unique_config_identities() -> None:
    protocol, _dataset, schedule = _registry_inputs()
    adapter = object()
    registry = build_suite_registry(
        schedule,
        protocol,
        route_bindings={
            "retriever.dense": RouteBinding(
                route_id="retriever.dense",
                implementation_id="retriever.dense",
                config_sha256="c" * 64,
                adapter=adapter,
            )
        },
        generation_config_sha256="d" * 64,
    )

    assert len(registry.plans) == 111
    assert len({plan.execution_config_sha256 for plan in registry.plans.values()}) == 111
    dense = registry.plan_for("system/dense/standard/seed-17")
    assert (dense.system_id, dense.ablation_id, dense.budget_id, dense.seed) == (
        "dense",
        None,
        "standard",
        17,
    )
    assert dense.route_id == "retriever.dense"
    assert dense.route_config_sha256 == "c" * 64
    assert dense.generation_config_sha256 == "d" * 64
    assert dense.executable is False
    assert any("CACHE_CONTROL_PORT" in item for item in dense.blockers)
    segr = registry.plan_for("system/segr/standard/seed-17")
    assert segr.weights_sha256 == "f" * 64
    ablation = registry.plan_for("ablation/A08/standard/seed-29")
    assert ablation.ablation_id == "A08"
    assert ablation.route_id == "agent.segr.ablation.A08"
    assert ablation.weights_sha256 == "f" * 64
    assert SYSTEM_ROUTES["hybrid_rerank"] == "retriever.hybrid_rerank"
    assert any(
        "B06_TRACE_PORT" in item
        for item in registry.plan_for("system/fixed_agent/standard/seed-17").blockers
    )
    assert _registered_suite_blocker(schedule, protocol, SimpleNamespace(registry=registry)) == (
        "BLOCKED_CACHE_CONTROL_PORT: suite registry has no frozen B04 cache-control profile"
    )
    assert _registered_suite_blocker(schedule, protocol, SimpleNamespace(registry=registry)) == (
        "BLOCKED_CACHE_CONTROL_PORT: suite registry has no frozen B04 cache-control profile"
    )


def test_registry_rejects_one_adapter_masquerading_as_two_systems() -> None:
    protocol, _dataset, schedule = _registry_inputs()
    shared_adapter = object()
    with pytest.raises(ValueError, match="adapter instance"):
        build_suite_registry(
            schedule,
            protocol,
            route_bindings={
                route_id: RouteBinding(
                    route_id=route_id,
                    implementation_id=route_id,
                    config_sha256="a" * 64,
                    adapter=shared_adapter,
                )
                for route_id in ("retriever.bm25", "retriever.dense")
            },
            generation_config_sha256="d" * 64,
        )


def test_route_binding_rejects_mismatched_implementation_and_bad_config_hash() -> None:
    with pytest.raises(ValueError, match="implementation identity"):
        RouteBinding(
            route_id="retriever.bm25",
            implementation_id="retriever.dense",
            config_sha256="a" * 64,
            adapter=object(),
        )
    with pytest.raises(ValueError, match="SHA-256"):
        RouteBinding(
            route_id="retriever.dense",
            implementation_id="retriever.dense",
            config_sha256="Z" * 64,
            adapter=object(),
        )
