from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from knowledge_weaver.models import RetrievalHit, ScoreDirection

RRF_K = 60


@dataclass(frozen=True, slots=True)
class _Contribution:
    channel: str
    rank: int
    raw_score: float
    score_direction: str
    rrf_score: float

    def as_dict(self) -> dict[str, str | int | float]:
        return {
            "channel": self.channel,
            "rank": self.rank,
            "raw_score": self.raw_score,
            "score_direction": self.score_direction,
            "rrf_score": self.rrf_score,
        }


@dataclass(slots=True)
class _FusionRow:
    score: float = 0.0
    evidence_ids: set[str] = field(default_factory=set)
    path_ids: set[str] = field(default_factory=set)
    contributions: list[_Contribution] = field(default_factory=list)


def reciprocal_rank_fusion(
    channel_hits: Mapping[str, Iterable[RetrievalHit]], *, limit: int | None = None
) -> list[RetrievalHit]:
    """Fuse independently ranked channels with fixed, equal-weight RRF (k=60)."""
    if limit is not None and not 1 <= limit <= 100:
        raise ValueError("limit must be in the range 1..100")

    fused: dict[str, _FusionRow] = {}
    for channel in sorted(channel_hits):
        if not channel.strip():
            raise ValueError("channel names must be non-empty")
        unique: dict[str, RetrievalHit] = {}
        for hit in channel_hits[channel]:
            previous = unique.get(hit.chunk_id)
            if previous is None or _dedupe_key(hit, channel) < _dedupe_key(previous, channel):
                unique[hit.chunk_id] = hit

        for chunk_id, hit in unique.items():
            raw_score, direction = _raw_score_and_direction(hit, channel)
            rrf_score = 1.0 / (RRF_K + hit.rank)
            row = fused.setdefault(chunk_id, _FusionRow())
            row.score += rrf_score
            row.evidence_ids.update(hit.evidence_ids)
            row.path_ids.update(hit.path_ids)
            row.contributions.append(
                _Contribution(
                    channel=channel,
                    rank=hit.rank,
                    raw_score=raw_score,
                    score_direction=direction.value,
                    rrf_score=rrf_score,
                )
            )

    ordered = sorted(fused.items(), key=lambda item: (-item[1].score, item[0]))
    if limit is not None:
        ordered = ordered[:limit]
    return [
        RetrievalHit(
            chunk_id=chunk_id,
            evidence_ids=sorted(row.evidence_ids),
            score=row.score,
            channel="hybrid",
            rank=rank,
            contributions=[
                contribution.as_dict()
                for contribution in sorted(row.contributions, key=lambda item: item.channel)
            ],
            path_ids=sorted(row.path_ids),
        )
        for rank, (chunk_id, row) in enumerate(ordered, start=1)
    ]


def _dedupe_key(hit: RetrievalHit, channel: str) -> tuple[int, str, float]:
    score, direction = _raw_score_and_direction(hit, channel)
    return hit.rank, direction.value, score


def _raw_score_and_direction(
    hit: RetrievalHit, channel: str
) -> tuple[float, ScoreDirection]:
    matching = [
        item
        for item in hit.contributions
        if item.get("channel") in {channel, hit.channel}
    ]
    contribution = matching[0] if matching else {}
    raw_score = contribution.get("raw_score", hit.score)
    direction_value = contribution.get("score_direction", ScoreDirection.higher.value)
    if (
        isinstance(raw_score, bool)
        or not isinstance(raw_score, (int, float))
        or not math.isfinite(float(raw_score))
    ):
        raise ValueError("hit raw_score must be finite numeric data")
    if direction_value in {"lower", "lower_is_better"}:
        direction = ScoreDirection.lower_is_better
    elif direction_value == "higher":
        direction = ScoreDirection.higher
    else:
        raise ValueError("hit score_direction must be higher or lower_is_better")
    return float(raw_score), direction
