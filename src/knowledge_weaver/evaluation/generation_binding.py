"""Verify persisted Generation identity and bind it to formal evaluation calls."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from knowledge_weaver.models.contracts import Generation, GenerationState

_UUID_TEXT = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class GenerationBindingError(ValueError):
    """A requested generation could not be proven from its persisted manifest."""


class PersistedGenerationLookup(Protocol):
    async def generation(self, generation_id: str) -> Generation: ...


@dataclass(frozen=True)
class VerifiedGeneration:
    generation: Generation
    generation_sha256: str

    @property
    def id(self) -> str:
        return self.generation.id


def valid_generation_binding(
    generation_id: str | None, generation_sha256: str | None
) -> bool:
    return bool(
        generation_id
        and _UUID_TEXT.fullmatch(generation_id)
        and generation_sha256
        and _SHA256.fullmatch(generation_sha256)
    )


def _member_sha256(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


def verify_generation_manifest(
    generation: Generation,
    *,
    observed_member_ids: Mapping[str, Sequence[str]] | None = None,
) -> VerifiedGeneration:
    """Recompute the persisted digest and validate any supplied Generation members."""
    if not isinstance(generation, Generation):
        raise GenerationBindingError("persisted Generation record has an invalid model")
    if not _UUID_TEXT.fullmatch(generation.id):
        raise GenerationBindingError("persisted Generation ID is not UUIDText")
    if generation.state not in {GenerationState.READY, GenerationState.RETIRED}:
        raise GenerationBindingError("persisted Generation is not queryable")

    manifest = dict(generation.index_manifest)
    stored_digest = manifest.pop("generation_hash", None)
    member_hashes = manifest.get("member_hashes")
    if not isinstance(stored_digest, str) or not _SHA256.fullmatch(stored_digest):
        raise GenerationBindingError("persisted Generation manifest has no valid digest")
    if not isinstance(member_hashes, Mapping):
        raise GenerationBindingError("persisted Generation manifest has no membership hashes")

    visible_members = {
        "chunk": generation.chunk_ids,
        "entity": generation.entity_ids,
        "relation": generation.relation_ids,
    }
    if observed_member_ids is not None:
        visible_members.update(
            {kind: list(ids) for kind, ids in observed_member_ids.items()}
        )
    for kind, ids in visible_members.items():
        if member_hashes.get(kind) != _member_sha256(ids):
            raise GenerationBindingError("persisted Generation membership does not match its manifest")

    payload = {
        "source_version_ids": generation.source_version_ids,
        "member_hashes": member_hashes,
        "index_manifest": manifest,
    }
    computed_digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if stored_digest != computed_digest:
        raise GenerationBindingError("persisted Generation manifest digest is inconsistent")
    return VerifiedGeneration(generation=generation, generation_sha256=computed_digest)


async def resolve_generation(
    lookup: PersistedGenerationLookup,
    generation_id: str | None,
    expected_sha256: str | None = None,
) -> VerifiedGeneration:
    """Load the UUID from persistence, derive its digest, and compare expectations."""
    if not generation_id or not _UUID_TEXT.fullmatch(generation_id):
        raise GenerationBindingError("requested Generation ID is not UUIDText")
    try:
        generation = await lookup.generation(generation_id)
    except Exception:  # noqa: BLE001 - persistence lookup failures must fail closed.
        raise GenerationBindingError("requested Generation does not exist or could not be read") from None
    if not isinstance(generation, Generation):
        raise GenerationBindingError("persisted Generation record has an invalid model")
    if generation.id != generation_id:
        raise GenerationBindingError("persisted Generation ID differs from the requested ID")
    verified = verify_generation_manifest(generation)
    if expected_sha256 is not None and verified.generation_sha256 != expected_sha256:
        raise GenerationBindingError("requested Generation digest differs from persisted content")
    return verified
