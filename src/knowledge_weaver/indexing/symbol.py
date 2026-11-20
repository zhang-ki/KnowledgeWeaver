from __future__ import annotations

import json
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from knowledge_weaver.models import IndexCandidate, IndexChannel, IndexQuery, ScoreDirection


def _norm(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


class SymbolIndex:
    """Exact and qualified-name prefix index over symbols grounded by mentions."""
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def build(self, corpus_id: str, generation_id: str, rows: list[dict[str, Any]]) -> str:
        path = self.root / corpus_id / generation_id / "symbol.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return str(path)

    async def search(self, query: IndexQuery) -> list[IndexCandidate]:
        if query.channel != IndexChannel.symbol:
            raise ValueError("symbol index only accepts the symbol channel")
        path = self.root / query.context.corpus_id / query.context.generation_id / "symbol.json"
        if not path.exists():
            return []
        symbol = _norm(query.symbol or query.text)
        allowed = None if query.context.source_ids is None else set(query.context.source_ids)
        scores: dict[str, float] = defaultdict(float)
        for row in json.loads(path.read_text(encoding="utf-8")):
            if allowed is not None and row["source_id"] not in allowed:
                continue
            name = _norm(row["name"])
            if name == symbol:
                scores[row["chunk_id"]] = max(scores[row["chunk_id"]], 2.0)
            elif name.startswith(symbol):
                scores[row["chunk_id"]] = max(scores[row["chunk_id"]], 1.0 / (1 + len(name) - len(symbol)))
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:query.top_k]
        return [IndexCandidate(chunk_id=key, rank=i, raw_score=score, score_direction=ScoreDirection.higher) for i, (key, score) in enumerate(ordered, 1)]


class EntityIndex:
    """Exact scoped canonical/alias lookup. Aliases never cross entity identity scope."""
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def build(self, corpus_id: str, generation_id: str, rows: list[dict[str, Any]]) -> str:
        path = self.root / corpus_id / generation_id / "entity.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return str(path)

    async def search(self, query: IndexQuery) -> list[IndexCandidate]:
        if query.channel != IndexChannel.entity:
            raise ValueError("entity index only accepts the entity channel")
        path = self.root / query.context.corpus_id / query.context.generation_id / "entity.json"
        if not path.exists():
            return []
        requested = set(query.entity_ids)
        needle = _norm(query.text)
        allowed = None if query.context.source_ids is None else set(query.context.source_ids)
        scores: dict[str, float] = defaultdict(float)
        for row in json.loads(path.read_text(encoding="utf-8")):
            if allowed is not None and row["source_id"] not in allowed:
                continue
            if requested and row["entity_id"] not in requested:
                continue
            names = [_norm(row["canonical_name"]), *(_norm(alias) for alias in row["aliases"])]
            if any(name == needle for name in names):
                scores[row["chunk_id"]] = max(scores[row["chunk_id"]], 1.0)
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:query.top_k]
        return [IndexCandidate(chunk_id=key, rank=i, raw_score=score, score_direction=ScoreDirection.higher) for i, (key, score) in enumerate(ordered, 1)]
