from __future__ import annotations

import re
from dataclasses import dataclass

from knowledge_weaver.core.ids import deterministic_id
from knowledge_weaver.models import Entity, Evidence

from ._common import normalize_surface

_ALIAS_MARKERS = re.compile(
    r"\b(?:aka|also\s+known\s+as|alias(?:ed)?\s+as)\b|又称|又名|亦称|別名|别名",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AlignmentDecision:
    mapping_id: str
    left_entity_id: str
    right_entity_id: str
    accepted: bool
    method: str | None
    evidence_id: str | None
    reason: str


def propose_text_alignment(
    left: Entity,
    right: Entity,
    *,
    evidence: Evidence | None = None,
    manual_mapping_id: str | None = None,
) -> AlignmentDecision:
    """Authorize a cross-source mapping only with explicit alias evidence or a human map.

    This returns an auditable mapping decision; it never changes entity IDs or
    merges records. Callers must perform any storage-level alignment separately.
    """
    left_id, right_id = sorted((left.id, right.id))
    if left.kind != right.kind:
        return _decision(left_id, right_id, False, None, None, "entity kinds differ")
    if left.id == right.id:
        return _decision(left_id, right_id, True, "same_identity", None, "entities already share identity")
    if left.namespace == right.namespace:
        return _decision(
            left_id,
            right_id,
            False,
            None,
            None,
            "same-namespace entities remain distinct without an explicit map",
        )
    if manual_mapping_id is not None and manual_mapping_id.strip():
        return _decision(
            left_id,
            right_id,
            True,
            "MANUAL",
            None,
            "explicit human mapping supplied",
            mapping_evidence=manual_mapping_id.strip(),
        )
    if evidence is not None:
        normalized_quote = normalize_surface(evidence.quote)
        left_surfaces = [left.canonical_name, *left.aliases]
        right_surfaces = [right.canonical_name, *right.aliases]
        names_present = any(normalize_surface(value) in normalized_quote for value in left_surfaces) and any(
            normalize_surface(value) in normalized_quote for value in right_surfaces
        )
        if names_present and _ALIAS_MARKERS.search(evidence.quote):
            return _decision(
                left_id,
                right_id,
                True,
                "ALIAS_EVIDENCE",
                evidence.id,
                "unique source evidence explicitly states an alias relationship",
                mapping_evidence=evidence.id,
            )
    return _decision(
        left_id,
        right_id,
        False,
        None,
        evidence.id if evidence is not None else None,
        "cross-source name similarity is insufficient without explicit alias evidence or a human map",
    )


def _decision(
    left_id: str,
    right_id: str,
    accepted: bool,
    method: str | None,
    evidence_id: str | None,
    reason: str,
    *,
    mapping_evidence: str | None = None,
) -> AlignmentDecision:
    mapping_id = deterministic_id(
        "text_alignment",
        {
            "entities": [left_id, right_id],
            "accepted": accepted,
            "method": method,
            "evidence": mapping_evidence,
        },
    )
    return AlignmentDecision(
        mapping_id=mapping_id,
        left_entity_id=left_id,
        right_entity_id=right_id,
        accepted=accepted,
        method=method,
        evidence_id=evidence_id,
        reason=reason,
    )
