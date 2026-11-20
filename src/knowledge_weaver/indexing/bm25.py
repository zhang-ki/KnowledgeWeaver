from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from knowledge_weaver.models import IndexCandidate, IndexChannel, IndexQuery, ScoreDirection

TOKENIZER_VERSION = "kw-bm25-latin-camel-snake-cjk-char-v1"
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_LATIN = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Split Latin identifiers/words and retain each CJK ideograph as a term."""
    result: list[str] = []
    for part in _CAMEL.sub(" ", text.replace("_", " ")).split():
        latin = _LATIN.findall(part)
        if latin:
            result.extend(token.casefold() for token in latin)
        for char in part:
            if "CJK" in unicodedata.name(char, "") and unicodedata.category(char).startswith("L"):
                result.append(char)
    return result


class Bm25Index:
    def __init__(self, root: str | Path, *, k1: float = 1.5, b: float = 0.75) -> None:
        self.root = Path(root)
        self.k1 = k1
        self.b = b

    def build(self, corpus_id: str, generation_id: str, documents: list[dict[str, Any]]) -> str:
        rows = []
        for item in sorted(documents, key=lambda row: row["chunk_id"]):
            terms = tokenize(item["text"])
            rows.append({**item, "terms": terms, "length": len(terms)})
        payload = {"version": TOKENIZER_VERSION, "corpus_id": corpus_id, "generation_id": generation_id, "documents": rows}
        target = self.root / corpus_id / generation_id / "bm25.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        temporary.replace(target)
        return str(target)

    async def search(self, query: IndexQuery) -> list[IndexCandidate]:
        if query.channel != IndexChannel.bm25:
            raise ValueError("BM25 index only accepts the bm25 channel")
        path = self.root / query.context.corpus_id / query.context.generation_id / "bm25.json"
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != TOKENIZER_VERSION:
            raise ValueError("BM25 tokenizer version mismatch")
        terms = tokenize(query.text)
        if not terms:
            return []
        docs = payload["documents"]
        allowed = None if query.context.source_ids is None else set(query.context.source_ids)
        docs = [row for row in docs if allowed is None or row["source_id"] in allowed]
        n = len(docs)
        avgdl = sum(row["length"] for row in docs) / n if n else 0.0
        df = Counter(term for row in docs for term in set(row["terms"]))
        scored: list[tuple[str, float]] = []
        for row in docs:
            counts = Counter(row["terms"])
            score = 0.0
            for term in terms:
                frequency = counts[term]
                if not frequency:
                    continue
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                denominator = frequency + self.k1 * (1 - self.b + self.b * row["length"] / max(avgdl, 1))
                score += idf * frequency * (self.k1 + 1) / denominator
            if score > 0:
                scored.append((row["chunk_id"], score))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return [IndexCandidate(chunk_id=chunk_id, rank=rank, raw_score=score, score_direction=ScoreDirection.higher) for rank, (chunk_id, score) in enumerate(scored[: query.top_k], 1)]
