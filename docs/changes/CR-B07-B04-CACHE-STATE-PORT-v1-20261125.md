# CR-B07-B04-CACHE-STATE-PORT-v1

## Status

Implemented as a scoped B04 cache-control port and B07 evaluation receipt on 2026-11-25. Full cold/warm enforcement remains incomplete for the current retrieval stacks, so this does not unblock formal suites.

## Original gap

At request time, the public `Retriever.retrieve(RetrievalQuery)` port had no cache-state input, reset operation, warm-up operation, or cache-state verification output. Passing the label through `QueryRunRecord` alone could not enforce or prove the requested state.

## Requested public contract

Add a cache-control capability to the B04-owned retrieval boundary, for example an immutable `CacheProfile` with requested state (`cold` or `warm`) plus a verifiable execution receipt. A cold run must clear or isolate all relevant retriever/index caches before the query. A warm run must define and execute the frozen warm-up protocol before the measured query. The receipt must identify the cache-control implementation/config SHA and state that was actually applied.

If complete cold/warm control belongs to the composition layer rather than an individual Retriever, expose one public B04 cache-control service that covers all selected channels and the shared index/store caches. The executor must fail closed when the requested state cannot be applied or verified.

## Implemented scope

- `GraphRetriever` exposes reset, prewarm, and membership verification for its bounded process-local `_snapshot_cache`. Cold reset clears the mapping and confirms it is empty. Warm preparation loads the graph view for the same `QueryContext` and confirms the matching cache key is resident.
- `CacheStateController` orders controlled components by stable component ID. Its receipt records route, requested/applied state, implementation SHA-256, config SHA-256, controlled and uncontrolled component IDs, warm-up sequence, and the verified result of each operation.
- `PublicPortBenchmarkExecutor` prepares cache state before measured retrieval. It rejects absent receipts, route/state/config mismatches, or incomplete scope before calling `Retriever.retrieve`. The runner persists partial failure receipts, and `AttemptOutcome` rejects a completed attempt without a complete matching receipt.
- Dense Dev reports derive `cache_state_enforced` from every attempt's actual receipt. A cold/warm label alone cannot set it to true.

## Cache inventory and frozen warm-up order

| Route component | Controlled cache | Uncontrolled scope reported by B04 |
|---|---|---|
| Graph | `GraphRetriever._snapshot_cache`; reset or prewarm by the current generation/source/inference context | graph-store backend, EntityIndex storage cache, KnowledgeStore backend cache |
| Dense | None through current public adapters | shared Embedding Provider cache, Qdrant server cache, KnowledgeStore backend cache |
| BM25 | None through current public adapters; BM25 JSON is read per search | filesystem/OS page cache, KnowledgeStore backend cache |
| Symbol / Entity | None through current public adapters; index JSON is read per search | filesystem/OS page cache, KnowledgeStore backend cache |
| Hybrid | union of channel controls | union of channel scope plus optional reranker shared-provider cache |
| B07 answer generation | Outside B04 retrieval control | shared GenerationProvider cache; included by the evaluator because end-to-end latency includes answer generation |

For warm requests, B04 follows the frozen controlled-component order (lexicographic component ID order). The current real Graph route has one controlled step, `retriever.graph/graph-snapshot-cache`; it is followed by verification of the same query context's cache key. Other current routes have no complete controllable scope.

## Remaining gate

The current public ports cannot clear or verify shared Qdrant, PostgreSQL, filesystem/OS, Embedding Provider, optional reranker, or answer GenerationProvider caches. A partial receipt may show that the Graph snapshot cache operation succeeded, but it sets `complete=false`. `PublicPortBenchmarkExecutor` fails with `CACHE_SCOPE_INCOMPLETE` before making the measured retrieval call. Missing cache receipts and wrong config hashes also fail before retrieval. Formal suite blockers and B07 quality thresholds remain unchanged; formal B07 runs are still blocked until all required shared caches can be controlled and the complete profile is integrated into the frozen suite registry.

## Focused acceptance evidence

1. B04 tests cover repeatable cold reset, deterministic warm-up order, successful Graph snapshot cache prewarm, and failed component verification.
2. B07 tests cover per-attempt receipt propagation, fail-closed incomplete Dense scope, missing receipt and config-hash mismatch before retrieval, and the complete-receipt model requirement.
3. Targeted B04/B07 tests pass without connecting to PostgreSQL, Qdrant, or any provider. No formal Test data, paid provider, or quality threshold was used or changed.

The current suite registry continues to block formal runs with `BLOCKED_CACHE_CONTROL_PORT`; this implementation does not bypass that gate.
