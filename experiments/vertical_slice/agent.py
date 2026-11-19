"""Tiny SEGR-style investigation loop isolated from production AgentService."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from experiments.vertical_slice.models import Citation, Claim, Corpus, Evidence, InvestigationResult
from experiments.vertical_slice.providers import FakeGenerationProvider, LLMRequest
from experiments.vertical_slice.retrieval import (
    BM25Index,
    GraphRetriever,
    InMemoryDenseIndex,
    SearchHit,
    detect_relation_conflicts,
    tokenize,
)

SYMBOLS = ("EvidenceRecord", "SourceSpan", "DemoLookupCache", "bind_source_span")
POLICY_FINGERPRINT = "p3-public-prior-v1;features=12;lambda_cost=0.3;tau=0"


@dataclass(frozen=True)
class InvestigationBudget:
    max_steps: int = 6
    max_tool_calls: int = 12
    max_llm_calls: int = 8
    max_total_tokens: int = 12000
    deadline_seconds: float = 60.0


class PriorPolicy:
    """The frozen cold-start prior, with fixed cost and stable action ordering."""

    def candidates(
        self,
        query: str,
        exact_symbol: bool,
        cross_source: bool,
        used: list[str],
        ema_by_tool: dict[str, float],
        budget: InvestigationBudget,
        step: int,
    ) -> list[dict[str, Any]]:
        remaining_tokens = max(1, budget.max_total_tokens - 80 * step)
        actions = []
        for tool in ("bm25", "dense"):
            match = (exact_symbol and tool == "bm25") or (cross_source and tool == "dense")
            prior = 0.6 if exact_symbol and tool == "bm25" else 0.4 if match else 0.2
            estimated_tokens = max(32, len(query) // 3)
            estimated_ms = 30 if tool == "bm25" else 80
            cost = 0.5 * estimated_tokens / remaining_tokens + 0.5 * estimated_ms / max(1.0, budget.deadline_seconds * 1000)
            duplicate_risk = 1.0 if tool in used else 0.0
            ema = ema_by_tool.get(tool, 0.0)
            predicted_gain = max(-1.0, min(1.0, 0.8 * prior + 0.2 * ema))
            utility = predicted_gain - 0.3 * cost - 0.1 * duplicate_risk
            features = [
                1.0,
                1.0 if step == 0 else 0.5,
                1.0 if match else 0.0,
                0.0,
                0.0,
                1.0 if cross_source else 0.0,
                0.0,
                min(1.0, len(set(tokenize(query))) / 12),
                1.0 if exact_symbol else 0.0,
                1.0 if tool == "graph" and cross_source else 0.0,
                min(1.0, len(used) / max(1, budget.max_steps)),
                max(0.0, 1.0 - 80 * step / max(1, budget.max_total_tokens)),
            ]
            actions.append(
                {
                    "tool": tool,
                    "query": query,
                    "top_k": 10,
                    "prior_gain": prior,
                    "ema_gain": ema,
                    "predicted_gain": predicted_gain,
                    "estimated_tokens": estimated_tokens,
                    "estimated_ms": estimated_ms,
                    "cost": round(cost, 6),
                    "utility": round(utility, 6),
                    "features": features,
                    "policy_fingerprint": POLICY_FINGERPRINT,
                }
            )
        return sorted(actions, key=lambda item: (-item["utility"], item["tool"]))

    def choose(self, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        return candidates[0] if candidates else None


class VerticalSliceAgent:
    def __init__(
        self,
        corpus: Corpus,
        bm25: BM25Index,
        dense: InMemoryDenseIndex,
        generator: Any | None = None,
        policy: PriorPolicy | None = None,
    ) -> None:
        self.corpus = corpus
        self.bm25 = bm25
        self.dense = dense
        self.generator = generator or FakeGenerationProvider()
        self.policy = policy or PriorPolicy()
        self.chunk_by_id = {item.id: item for item in corpus.chunks}
        self.evidence_by_id = {item.id: item for item in corpus.evidence}
        self.evidence_by_chunk: dict[str, list[Evidence]] = {}
        for evidence in corpus.evidence:
            self.evidence_by_chunk.setdefault(evidence.chunk_id, []).append(evidence)

    async def investigate(self, query: str, budget: InvestigationBudget | None = None) -> InvestigationResult:
        budget = budget or InvestigationBudget()
        started = time.monotonic()
        exact_symbol = any(symbol.casefold() in query.casefold() for symbol in SYMBOLS)
        cross_source = (
            ("pipeline" in query.casefold() or "ingestion" in query.casefold())
            and ("span" in query.casefold() or "evidence" in query.casefold())
        )
        used_tools: list[str] = []
        routes: list[str] = []
        actions: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        paths: list[dict[str, Any]] = []
        ema_by_tool: dict[str, float] = {}
        tool_calls = 0
        tokens = 0
        llm_calls = 0
        stop_reason = "completed"

        if budget.max_steps <= 0 or budget.max_tool_calls <= 0 or budget.max_total_tokens <= 0:
            return self._result("budget_exhausted", "The investigation stopped before retrieval because its hard budget was zero.", [], [], [], [], routes, actions, tool_calls, llm_calls, tokens, "budget_exhausted")

        candidates = self.policy.candidates(query, exact_symbol, cross_source, used_tools, ema_by_tool, budget, 0)
        chosen = self.policy.choose(candidates)
        if chosen is None:
            return self._result("insufficient", "No retrieval action is available.", [], [], ["No retrieval action is available."], [], routes, actions, tool_calls, llm_calls, tokens, "no_actions")
        if time.monotonic() - started >= budget.deadline_seconds:
            return self._result("budget_exhausted", "The investigation deadline expired before retrieval.", [], [], ["Deadline expired before retrieval."], [], routes, actions, tool_calls, llm_calls, tokens, "deadline_exceeded")
        if chosen["estimated_tokens"] > budget.max_total_tokens:
            return self._result("budget_exhausted", "The first retrieval action did not fit the token budget.", [], [], ["Retrieval was not called because its token reserve exceeded the budget."], [], routes, actions, tool_calls, tokens, "budget_exhausted")

        action_started = time.perf_counter()
        initial_hits = await self._retrieve(chosen["tool"], query, chosen["top_k"])
        tool_calls += 1
        tokens += chosen["estimated_tokens"]
        used_tools.append(chosen["tool"])
        routes.append(chosen["tool"])
        previous_evidence_ids = set(evidence_ids)
        previous_kinds = {self.evidence_by_id[item].source_kind for item in evidence_ids}
        self._add_hits(initial_hits, evidence_ids)
        new_ids = [item for item in evidence_ids if item not in previous_evidence_ids]
        new_kinds = {self.evidence_by_id[item].source_kind for item in new_ids}
        reward = 0.9 if new_kinds - previous_kinds else 0.7 if new_ids else 0.0
        ema_by_tool[chosen["tool"]] = 0.5 * reward + 0.5 * ema_by_tool.get(chosen["tool"], 0.0)
        actions.append(
            self._action_record(
                chosen,
                candidates,
                initial_hits,
                tool_calls,
                tokens,
                (time.perf_counter() - action_started) * 1000,
                new_ids,
                reward,
                ema_by_tool[chosen["tool"]],
                budget.max_total_tokens - tokens,
            )
        )

        seed_found = any("ingestion pipeline" in self.chunk_by_id[item.chunk_id].text.casefold() for item in initial_hits)
        if cross_source and seed_found:
            graph_ema_before = ema_by_tool.get("graph", 0.0)
            graph_predicted_gain = 0.8 * 0.4 + 0.2 * graph_ema_before
            remaining_ms = max(1.0, budget.deadline_seconds * 1000 - (time.monotonic() - started) * 1000)
            graph_cost = 0.5 * 64 / max(1, budget.max_total_tokens - tokens) + 0.5 * 40 / remaining_ms
            graph_utility = graph_predicted_gain - 0.3 * graph_cost
            graph_score = {
                "tool": "graph",
                "prior_gain": 0.4,
                "ema_gain_before": graph_ema_before,
                "predicted_gain": graph_predicted_gain,
                "cost": round(graph_cost, 6),
                "utility": round(graph_utility, 6),
                "features": [
                    1.0,
                    1.0,
                    1.0,
                    0.5,
                    0.0,
                    1.0,
                    graph_ema_before,
                    min(1.0, len(set(tokenize(query))) / 12),
                    0.0,
                    1.0,
                    min(1.0, len(used_tools) / max(1, budget.max_steps)),
                    max(0.0, 1.0 - tokens / max(1, budget.max_total_tokens)),
                ],
                "policy_fingerprint": POLICY_FINGERPRINT,
            }
            if len(actions) >= budget.max_steps or tool_calls >= budget.max_tool_calls or tokens + 64 > budget.max_total_tokens:
                stop_reason = "budget_exhausted"
            elif time.monotonic() - started >= budget.deadline_seconds:
                stop_reason = "deadline_exceeded"
            elif graph_utility <= 0.0:
                stop_reason = "best_utility_below_tau"
                actions.append(
                    {
                        "tool": "graph",
                        "candidate_scores": [graph_score],
                        "selected": False,
                        "remaining_tokens": budget.max_total_tokens - tokens,
                        "stop_reason": "best_utility_below_tau",
                    }
                )
            else:
                graph_started = time.perf_counter()
                graph_paths = GraphRetriever(self.corpus).two_hop(query)
                graph_evidence = [item for path in graph_paths for item in path.evidence_ids]
                paths = [
                    {
                        "path_id": path.id,
                        "entity_names": list(path.entity_names),
                        "relation_ids": list(path.relation_ids),
                        "evidence_ids": list(path.evidence_ids),
                        "source_kinds": list(path.source_kinds),
                    }
                    for path in graph_paths
                ]
                previous_evidence_ids = set(evidence_ids)
                previous_kinds = {self.evidence_by_id[item].source_kind for item in evidence_ids}
                evidence_ids.extend(item for item in graph_evidence if item not in evidence_ids)
                new_ids = [item for item in evidence_ids if item not in previous_evidence_ids]
                new_kinds = {self.evidence_by_id[item].source_kind for item in new_ids}
                reward = 0.9 if new_kinds - previous_kinds else 0.4 if graph_paths else 0.0
                ema_by_tool["graph"] = 0.5 * reward + 0.5 * graph_ema_before
                tool_calls += 1
                tokens += 64
                routes.append("graph")
                actions.append(
                    {
                        "tool": "graph",
                        "query": query,
                        "top_k": 10,
                        "candidate_scores": [graph_score],
                        "returned_path_count": len(paths),
                        "returned_paths": paths,
                        "returned_evidence_ids": graph_evidence,
                        "new_evidence_ids": new_ids,
                        "observed_reward_proxy": reward,
                        "ema_after": ema_by_tool["graph"],
                        "actual_elapsed_ms": round((time.perf_counter() - graph_started) * 1000, 3),
                        "estimated_tokens": 64,
                        "remaining_tokens": budget.max_total_tokens - tokens,
                        "policy_fingerprint": POLICY_FINGERPRINT,
                        "stop_reason": "path_found" if paths else "no_closed_cross_source_path",
                    }
                )
        elif cross_source:
            stop_reason = "budget_exhausted" if len(actions) >= budget.max_steps or tool_calls >= budget.max_tool_calls else "no_graph_seed"

        if stop_reason == "budget_exhausted":
            return self._result("budget_exhausted", "The investigation stopped at its hard budget before all planned retrieval actions completed.", [], [], ["A required retrieval step did not fit the remaining budget."], [], routes, actions, tool_calls, llm_calls, tokens, stop_reason)
        if stop_reason == "no_graph_seed":
            return self._result("insufficient", "The initial retrieval did not find an entity seed for the required cross-source path.", [], [], ["No graph seed was found; the two-hop claim remains unresolved."], [], routes, actions, tool_calls, llm_calls, tokens, stop_reason)
        if stop_reason == "best_utility_below_tau":
            return self._result("insufficient", "The best follow-up action was below the frozen prior's stop threshold; the cross-source path remains unresolved.", [], [], ["No follow-up action cleared tau=0."], [], routes, actions, tool_calls, tokens, stop_reason)
        if stop_reason == "deadline_exceeded":
            return self._result("budget_exhausted", "The investigation deadline expired before graph retrieval.", [], [], ["Deadline expired before the graph action."], [], routes, actions, tool_calls, tokens, stop_reason)

        if time.monotonic() - started >= budget.deadline_seconds:
            return self._result("budget_exhausted", "The investigation deadline expired before answer generation.", [], [], ["Deadline expired before answer generation."], [], routes, actions, tool_calls, llm_calls, tokens, "deadline_exceeded")
        if tokens >= budget.max_total_tokens or tool_calls > budget.max_tool_calls:
            stop_reason = "budget_exhausted"
        if stop_reason == "budget_exhausted":
            return self._result("budget_exhausted", "The investigation stopped at its hard budget before answer generation.", [], [], ["Evidence collection stopped at the hard budget."], [], routes, actions, tool_calls, llm_calls, tokens, stop_reason)

        conflicts = self._relevant_conflicts(evidence_ids)
        if not evidence_ids:
            return self._result("insufficient", "The corpus contains no supporting evidence for this question.", [], [], ["No source evidence matched the question."], conflicts, routes, actions, tool_calls, llm_calls, tokens, "insufficient_evidence")

        estimated_context_tokens = max(1, len(json.dumps({"evidence_ids": evidence_ids, "paths": paths}, ensure_ascii=False)) // 4)
        if llm_calls >= budget.max_llm_calls or tokens + estimated_context_tokens + 500 > budget.max_total_tokens:
            return self._result("budget_exhausted", "The investigation reached its hard budget before answer generation.", [], [], ["Answer generation was not called because budget was exhausted."], conflicts, routes, actions, tool_calls, llm_calls, tokens, "budget_exhausted")

        context = {
            "query": query,
            "evidence": [self._evidence_payload(item) for item in dict.fromkeys(evidence_ids) if item in self.evidence_by_id],
            "paths": paths,
            "conflicts": conflicts,
            "unresolved": ["Which cache implementation is authoritative?"] if conflicts else [],
        }
        request = LLMRequest(
            "Return JSON claims with evidence_ids drawn only from untrusted_context. If evidence conflicts, state the conflict and do not choose a winner. If unsupported, report insufficient.",
            json.dumps(context, ensure_ascii=False),
            {"type": "object", "required": ["status", "answer", "claims", "unanswered"], "claim_fields": ["text", "evidence_ids", "relation_ids"]},
            500,
            max(0.01, budget.deadline_seconds - (time.monotonic() - started)),
        )
        llm_calls += 1
        try:
            generated = await self.generator.generate(request)
        except TimeoutError:
            return self._result("insufficient", "The answer provider timed out; no generated claim is trusted.", [], [], ["Answer generation timed out."], conflicts, routes, actions, tool_calls, llm_calls, tokens, "deadline_exceeded")
        except Exception as exc:  # noqa: BLE001 - every adapter failure must fail closed
            return self._result("insufficient", "The answer provider failed; no generated claim is trusted.", [], [], [f"Provider failure: {type(exc).__name__}."], conflicts, routes, actions, tool_calls, llm_calls, tokens, "provider_unavailable")

        relation_ids = [item for path in paths for item in path["relation_ids"]]
        relation_ids.extend(
            relation_id
            for conflict in conflicts
            for side in conflict["evidence_sides"]
            for relation_id in side.get("relation_ids", [])
        )
        claims = self._validate_claims(generated.parsed_json.get("claims", []), evidence_ids, relation_ids)
        if generated.usage.get("count_kind") == "exact":
            tokens += int(generated.usage.get("input_tokens", 0)) + int(generated.usage.get("output_tokens", 0))
        else:
            tokens += estimated_context_tokens + request.max_output_tokens
        status = "conflicted" if conflicts else str(generated.parsed_json.get("status", "insufficient"))
        answer = str(generated.parsed_json.get("answer", ""))
        unanswered = list(generated.parsed_json.get("unanswered", []))
        if not claims:
            status = "insufficient"
            answer = "The response did not contain a verifiable source citation."
            unanswered = unanswered or ["No claim had a valid evidence citation."]
        if status not in {"answered", "insufficient", "conflicted", "budget_exhausted"}:
            status = "insufficient"
        if status == "conflicted":
            stop_reason = "conflict_disclosed"
        elif status == "answered":
            stop_reason = "sufficient_evidence"
        citations = self._citations([item for claim in claims for item in claim.evidence_ids])
        return self._result(status, answer, claims, citations, unanswered, conflicts, routes, actions, tool_calls, llm_calls, tokens, stop_reason)

    async def _retrieve(self, tool: str, query: str, top_k: int) -> list[SearchHit]:
        if tool == "bm25":
            return self.bm25.search(query, top_k)
        if tool == "dense":
            hits = await self.dense.search(query, top_k, self.corpus.generation_id)
            query_terms = set(tokenize(query))
            relevant = []
            for hit in hits:
                chunk = self.chunk_by_id[hit.chunk_id]
                if query_terms.intersection(tokenize(chunk.text)):
                    relevant.append(hit)
            return relevant
        return []

    def _add_hits(self, hits: list[SearchHit], ledger: list[str]) -> None:
        for hit in hits:
            for evidence in self.evidence_by_chunk.get(hit.chunk_id, []):
                if evidence.id not in ledger:
                    ledger.append(evidence.id)

    def _relevant_conflicts(self, evidence_ids: list[str]) -> list[dict[str, Any]]:
        allowed = set(evidence_ids)
        result = []
        for conflict in detect_relation_conflicts(self.corpus):
            sides = [side for side in conflict["evidence_sides"] if set(side["evidence_ids"]) <= allowed]
            source_kinds = {side["source_kind"] for side in sides}
            if len(sides) >= 2 and len(source_kinds) >= 2:
                result.append({**conflict, "evidence_sides": sides})
        return result

    def _evidence_payload(self, evidence_id: str) -> dict[str, Any]:
        item = self.evidence_by_id[evidence_id]
        return {"id": item.id, "quote": item.quote, "locator": item.locator, "relative_path": item.relative_path, "source_kind": item.source_kind}

    def _validate_claims(self, raw_claims: Any, allowed_evidence: list[str], allowed_relations: list[str]) -> list[Claim]:
        if not isinstance(raw_claims, list):
            return []
        allowed = set(allowed_evidence)
        claims = []
        for item in raw_claims:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                continue
            cited = tuple(dict.fromkeys(value for value in item.get("evidence_ids", []) if value in allowed and value in self.evidence_by_id))
            if cited:
                relation_ids = tuple(dict.fromkeys(value for value in item.get("relation_ids", []) if value in set(allowed_relations)))
                claims.append(Claim(item["text"], cited, relation_ids))
        return claims

    def _citations(self, evidence_ids: list[str]) -> list[Citation]:
        seen: set[str] = set()
        citations = []
        for evidence_id in evidence_ids:
            if evidence_id in seen or evidence_id not in self.evidence_by_id:
                continue
            seen.add(evidence_id)
            item = self.evidence_by_id[evidence_id]
            citations.append(Citation(item.id, item.relative_path, item.quote, item.locator))
        return citations

    @staticmethod
    def _action_record(
        action: dict[str, Any],
        candidates: list[dict[str, Any]],
        hits: list[SearchHit],
        tool_calls: int,
        tokens: int,
        elapsed_ms: float,
        new_evidence_ids: list[str],
        reward: float,
        ema_after: float,
        remaining_tokens: int,
    ) -> dict[str, Any]:
        return {
            "tool": action["tool"],
            "query": action["query"],
            "top_k": action["top_k"],
            "candidate_scores": [
                {
                    "tool": candidate["tool"],
                    "prior_gain": candidate["prior_gain"],
                    "ema_gain_before": candidate["ema_gain"],
                    "predicted_gain": candidate["predicted_gain"],
                    "cost": candidate["cost"],
                    "utility": candidate["utility"],
                    "features": candidate["features"],
                    "policy_fingerprint": candidate["policy_fingerprint"],
                }
                for candidate in candidates
            ],
            "returned_chunk_ids": [item.chunk_id for item in hits],
            "returned_channels": [item.channel for item in hits],
            "tool_calls_used": tool_calls,
            "estimated_tokens_used": tokens,
            "remaining_tokens": remaining_tokens,
            "new_evidence_ids": new_evidence_ids,
            "observed_reward_proxy": reward,
            "ema_after": ema_after,
            "actual_elapsed_ms": round(elapsed_ms, 3),
            "stop_reason": "continue_or_finish",
        }

    def _result(
        self,
        status: str,
        answer: str,
        claims: list[Claim],
        citations: list[Citation],
        unresolved: list[str],
        conflicts: list[dict[str, Any]],
        routes: list[str],
        actions: list[dict[str, Any]],
        tool_calls: int,
        llm_calls: int,
        tokens: int,
        stop_reason: str,
    ) -> InvestigationResult:
        return InvestigationResult(
            status,
            answer,
            claims,
            citations,
            unresolved,
            conflicts,
            routes,
            actions,
            {"tool_calls": tool_calls, "llm_calls": llm_calls, "estimated_total_tokens": tokens},
            stop_reason,
            warnings=["provider=p3-offline-fake; not a real model result"],
        )


def make_agent(corpus: Corpus, generator: Any | None = None) -> VerticalSliceAgent:
    bm25 = BM25Index(corpus.chunks)
    dense = InMemoryDenseIndex(corpus.chunks, corpus.generation_id)
    return VerticalSliceAgent(corpus, bm25, dense, generator=generator)
