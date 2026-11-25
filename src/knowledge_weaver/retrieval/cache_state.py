"""Verifiable cache-state controls for the B04 retrieval boundary."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from knowledge_weaver.models import QueryContext

CacheState = Literal["cold", "warm"]
_SHA256 = r"^[0-9a-f]{64}$"


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _module_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class CacheStateOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    component_id: str = Field(min_length=1)
    operation: Literal["reset", "warm"]
    verified: bool
    failure_type: str | None = None


class CacheStateReceipt(BaseModel):
    """Receipt for the cache scope that B04 actually controlled for one attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route_id: str = Field(min_length=1)
    requested_state: CacheState
    applied_state: CacheState | None = None
    implementation_sha256: str = Field(pattern=_SHA256)
    config_sha256: str = Field(pattern=_SHA256)
    controlled_components: tuple[str, ...] = ()
    uncontrolled_components: tuple[str, ...] = ()
    warmup_sequence: tuple[str, ...] = ()
    operations: tuple[CacheStateOperation, ...] = ()
    complete: bool

    @model_validator(mode="after")
    def receipt_matches_observed_scope(self) -> CacheStateReceipt:
        if tuple(sorted(set(self.controlled_components))) != self.controlled_components:
            raise ValueError("controlled cache components must be unique and sorted")
        if tuple(sorted(set(self.uncontrolled_components))) != self.uncontrolled_components:
            raise ValueError("uncontrolled cache components must be unique and sorted")
        if self.warmup_sequence != self.controlled_components:
            raise ValueError("warm-up sequence must match the frozen controlled-component order")
        if [item.sequence for item in self.operations] != list(
            range(1, len(self.operations) + 1)
        ):
            raise ValueError("cache operations must have contiguous sequence numbers")
        expected_operation = "reset" if self.requested_state == "cold" else "warm"
        if any(item.operation != expected_operation for item in self.operations):
            raise ValueError("cache operation does not match the requested state")
        operation_ids = tuple(item.component_id for item in self.operations)
        if operation_ids != self.controlled_components[: len(operation_ids)]:
            raise ValueError("cache operations do not follow the frozen component order")
        all_verified = bool(self.controlled_components) and len(operation_ids) == len(
            self.controlled_components
        ) and all(item.verified for item in self.operations)
        if self.applied_state is not None and not all_verified:
            raise ValueError("applied cache state requires a verified operation for every component")
        if self.complete and (
            self.applied_state != self.requested_state
            or self.uncontrolled_components
            or not self.controlled_components
            or not all_verified
        ):
            raise ValueError("complete cache receipt must cover and verify its entire scope")
        return self


class CacheStateParticipant(Protocol):
    @property
    def component_id(self) -> str: ...

    @property
    def implementation_sha256(self) -> str: ...

    @property
    def config_sha256(self) -> str: ...

    async def reset_cache(self) -> bool: ...

    async def warm_cache(self, context: QueryContext) -> bool: ...


@dataclass(frozen=True, slots=True)
class CacheRouteProfile:
    route_id: str
    participants: tuple[CacheStateParticipant, ...]
    uncontrolled_components: tuple[str, ...]


class CacheStateController:
    """Apply cold/warm operations and emit a hash-bound B04 cache receipt."""

    def __init__(self, routes: dict[str, CacheRouteProfile]) -> None:
        profiles: dict[str, CacheRouteProfile] = {}
        for route_id, profile in routes.items():
            if route_id != profile.route_id:
                raise ValueError("cache route profile key differs from its route ID")
            component_ids = [item.component_id for item in profile.participants]
            if len(component_ids) != len(set(component_ids)):
                raise ValueError("cache participant IDs must be unique within a route")
            profiles[route_id] = CacheRouteProfile(
                route_id=route_id,
                participants=tuple(sorted(profile.participants, key=lambda item: item.component_id)),
                uncontrolled_components=tuple(sorted(set(profile.uncontrolled_components))),
            )
        self._routes = profiles
        self._implementation_sha256 = _canonical_sha256(
            {
                "controller_module_sha256": _module_sha256(),
                "prepare_source": inspect.getsource(type(self).prepare),
            }
        )
        self._config_sha256 = {
            route_id: self._config_sha256_for_profile(profile)
            for route_id, profile in profiles.items()
        }
        self._participant_fingerprints = {
            route_id: {
                participant.component_id: (
                    participant.implementation_sha256,
                    participant.config_sha256,
                )
                for participant in profile.participants
            }
            for route_id, profile in profiles.items()
        }

    @classmethod
    def for_retrievers(
        cls,
        retrievers: Mapping[str, object],
        *,
        uncontrolled_by_route: Mapping[str, tuple[str, ...]] | None = None,
    ) -> CacheStateController:
        """Build a fail-closed inventory from the actual route and evaluator graph."""
        external = uncontrolled_by_route or {}
        profiles = {}
        for route_id, adapter in retrievers.items():
            profile = _profile_for_retriever(route_id, adapter)
            profiles[route_id] = CacheRouteProfile(
                route_id=route_id,
                participants=profile.participants,
                uncontrolled_components=tuple(
                    sorted(
                        set(profile.uncontrolled_components)
                        | set(external.get(route_id, ()))
                    )
                ),
            )
        return cls(profiles)

    @property
    def implementation_sha256(self) -> str:
        return self._implementation_sha256

    def config_sha256_for(self, route_id: str) -> str:
        if route_id in self._config_sha256:
            return self._config_sha256[route_id]
        return _canonical_sha256(
            {"schema_version": 1, "route_id": route_id, "uncontrolled": ["unknown route"]}
        )

    async def prepare(
        self, route_id: str, context: QueryContext, requested_state: CacheState
    ) -> CacheStateReceipt:
        profile = self._routes.get(route_id)
        if profile is None:
            return CacheStateReceipt(
                route_id=route_id,
                requested_state=requested_state,
                implementation_sha256=self.implementation_sha256,
                config_sha256=self.config_sha256_for(route_id),
                uncontrolled_components=("unknown route cache scope",),
                warmup_sequence=(),
                complete=False,
            )

        receipt_operations: list[CacheStateOperation] = []
        for sequence, participant in enumerate(profile.participants, start=1):
            frozen_fingerprint = self._participant_fingerprints[route_id][
                participant.component_id
            ]
            current_fingerprint = (
                participant.implementation_sha256,
                participant.config_sha256,
            )
            failure_type: str | None = None
            try:
                if current_fingerprint != frozen_fingerprint:
                    verified = False
                    failure_type = "ConfigurationChanged"
                elif requested_state == "cold":
                    verified = await participant.reset_cache()
                    if not verified:
                        failure_type = "StateVerificationFailed"
                else:
                    verified = await participant.warm_cache(context)
                    if not verified:
                        failure_type = "StateVerificationFailed"
            except Exception as exc:  # noqa: BLE001 - retain only the exception type in the receipt.
                verified = False
                failure_type = type(exc).__name__
            receipt_operations.append(
                CacheStateOperation(
                    sequence=sequence,
                    component_id=participant.component_id,
                    operation="reset" if requested_state == "cold" else "warm",
                    verified=verified,
                    failure_type=failure_type,
                )
            )

        controlled_components = tuple(item.component_id for item in profile.participants)
        all_verified = len(receipt_operations) == len(controlled_components) and bool(
            controlled_components
        ) and all(item.verified for item in receipt_operations)
        applied_state = requested_state if all_verified else None
        return CacheStateReceipt(
            route_id=route_id,
            requested_state=requested_state,
            applied_state=applied_state,
            implementation_sha256=self.implementation_sha256,
            config_sha256=self.config_sha256_for(route_id),
            controlled_components=controlled_components,
            uncontrolled_components=profile.uncontrolled_components,
            warmup_sequence=controlled_components,
            operations=tuple(receipt_operations),
            complete=all_verified and not profile.uncontrolled_components,
        )

    def _config_sha256_for_profile(self, profile: CacheRouteProfile) -> str:
        return _canonical_sha256(
            {
                "schema_version": 1,
                "route_id": profile.route_id,
                "controller_implementation_sha256": self.implementation_sha256,
                "participants": [
                    {
                        "component_id": item.component_id,
                        "implementation_sha256": item.implementation_sha256,
                        "config_sha256": item.config_sha256,
                    }
                    for item in profile.participants
                ],
                "uncontrolled_components": profile.uncontrolled_components,
                "warmup_sequence": [item.component_id for item in profile.participants],
                "cold_operation": "reset_cache_and_verify_empty",
                "warm_operation": "warm_cache_for_current_query_context_and_verify_hit",
            }
        )


class _GraphSnapshotCacheParticipant:
    def __init__(self, component_id: str, retriever: object) -> None:
        from knowledge_weaver.graph.retriever import GraphRetriever, _cache_key

        if not isinstance(retriever, GraphRetriever):
            raise TypeError("Graph snapshot cache participant requires GraphRetriever")
        self.component_id = component_id
        self.retriever = retriever
        source = "\n".join(
            (
                inspect.getsource(GraphRetriever),
                inspect.getsource(_cache_key),
            )
        )
        self.implementation_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()

    @property
    def config_sha256(self) -> str:
        return _canonical_sha256(
            {
                "component_id": self.component_id,
                "cache_capacity": self.retriever.cache_capacity,
                "cache_key": ["corpus_id", "generation_id", "source_ids", "include_inferred"],
            }
        )

    async def reset_cache(self) -> bool:
        self.retriever.reset_snapshot_cache()
        return self.retriever.snapshot_cache_size == 0

    async def warm_cache(self, context: QueryContext) -> bool:
        return await self.retriever.prewarm_snapshot_cache(context)


def _profile_for_retriever(route_id: str, adapter: object) -> CacheRouteProfile:
    from knowledge_weaver.graph.retriever import GraphRetriever
    from knowledge_weaver.retrieval.hybrid import HybridRetriever
    from knowledge_weaver.retrieval.indexed import (
        Bm25Retriever,
        DenseRetriever,
        EntityRetriever,
        SymbolRetriever,
    )

    participants: list[CacheStateParticipant] = []
    uncontrolled: list[str] = []
    if isinstance(adapter, HybridRetriever):
        for channel_name, channel in sorted(adapter.channels.items()):
            nested = _profile_for_retriever(f"{route_id}/channel/{channel_name}", channel)
            participants.extend(nested.participants)
            uncontrolled.extend(nested.uncontrolled_components)
        if adapter.reranker is not None:
            uncontrolled.append(f"{route_id}/reranker/shared-provider-cache")
    elif isinstance(adapter, GraphRetriever):
        participant = _GraphSnapshotCacheParticipant(
            f"{route_id}/graph-snapshot-cache", adapter
        )
        participants.append(participant)
        uncontrolled.extend(
            (
                f"{route_id}/graph-store/backend-cache",
                f"{route_id}/entity-index/storage-cache",
                f"{route_id}/knowledge-store/backend-cache",
            )
        )
    elif isinstance(adapter, DenseRetriever):
        uncontrolled.extend(
            (
                f"{route_id}/embedding-provider/shared-cache",
                f"{route_id}/qdrant/server-cache",
                f"{route_id}/knowledge-store/backend-cache",
            )
        )
    elif isinstance(adapter, Bm25Retriever):
        uncontrolled.extend(
            (
                f"{route_id}/bm25-filesystem/page-cache",
                f"{route_id}/knowledge-store/backend-cache",
            )
        )
    elif isinstance(adapter, (EntityRetriever, SymbolRetriever)):
        uncontrolled.extend(
            (
                f"{route_id}/index-filesystem/page-cache",
                f"{route_id}/knowledge-store/backend-cache",
            )
        )
    else:
        uncontrolled.append(f"{route_id}/unknown-cache-scope")
    return CacheRouteProfile(
        route_id=route_id,
        participants=tuple(participants),
        uncontrolled_components=tuple(sorted(set(uncontrolled))),
    )
