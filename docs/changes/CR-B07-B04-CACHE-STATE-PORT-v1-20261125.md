# CR-B07-B04-CACHE-STATE-PORT-v1

## Status

Requested as a separate interface change from the B07 executor continuation on 2026-11-25. This Worktree does not modify B04-owned ports or implementation.

## Current gap

The public `Retriever.retrieve(RetrievalQuery)` port has no cache-state input, reset operation, warm-up operation, or cache-state verification output. B07's frozen matrix records both cold and warm latency measurements. Passing the label through `QueryRunRecord` alone cannot enforce or prove the requested state, including for Dense, BM25, Hybrid, and Graph routes.

## Requested public contract

Add a cache-control capability to the B04-owned retrieval boundary, for example an immutable `CacheProfile` with requested state (`cold` or `warm`) plus a verifiable execution receipt. A cold run must clear or isolate all relevant retriever/index caches before the query. A warm run must define and execute the frozen warm-up protocol before the measured query. The receipt must identify the cache-control implementation/config SHA and state that was actually applied.

If complete cold/warm control belongs to the composition layer rather than an individual Retriever, expose one public B04 cache-control service that covers all selected channels and the shared index/store caches. The executor must fail closed when the requested state cannot be applied or verified.

## Required acceptance evidence

1. B04 contract tests show a cold request resets the declared cache scope and a warm request follows the declared warm-up sequence.
2. The returned receipt distinguishes requested from applied state and binds the cache-control config SHA.
3. Negative tests prove unsupported or partially applied cache states fail before query results are marked usable for B07.
4. B04 retrieval tests and the B07 registry tests pass with no change to the frozen B07 cache-state dimensions or quality thresholds.

Until this CR is implemented and integrated by the B04 owner, formal B07 suite plans remain blocked with `BLOCKED_CACHE_CONTROL_PORT`. The Dev-only Dense slice can exercise the route and reports `cache_state_enforced=false`; its `cold` label is a requested profile only.
