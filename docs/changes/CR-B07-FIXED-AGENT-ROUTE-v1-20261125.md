# CR-B07-FIXED-AGENT-ROUTE-v1

## Status

Implemented in the isolated B07 branch on 2026-11-25; awaiting integration review. This implements the frozen fixed_agent action order and clears its B06 trace-route blocker when the exact route adapter is registered. B04 cache-state coverage remains incomplete, so fixed_agent formal suites remain blocked.

## Frozen contract and prior gap

The frozen behavior in docs/planning/EVALUATION_PLAN.md is a hard-budget fixed sequence: hybrid → graph → one query expansion → rerank. The previous B06 FixedPolicy instead ranked BM25, Dense, Symbol, Entity, Graph, and Rerank as separate actions. It had no action that represented the existing B04 HybridRetriever RRF result, so reusing FixedPolicy or labeling one of its channel actions “hybrid” would misreport execution.

## Implemented public action and route

- Added ActionTool.hybrid to the public action enum. AgentExecutionTraceStep.source can now identify the composite action without changing or inferring the returned hit channel. B07 scoring still reads the actual fused RetrievalHit values and their channel contributions.
- agent.fixed is now a supported immutable AgentBehaviorProfile. Its canonical config SHA binds the phase sequence, RRF without embedded reranking, top_k=10, Graph max_hops=2, one expansion capped at three terms, and disabled soft stopping. A profile with a different route/config SHA is rejected before retrieval or generation-provider calls.
- The fixed route requires an actual B04 HybridRetriever in the AgentServiceImpl retriever map under “hybrid”. That object must contain exactly bm25, dense, symbol, and entity channels and must not embed a reranker. The AgentServiceImpl separately requires the frozen final reranker before the first action. Missing or mismatched component shape fails closed.
- The action sequence is hybrid RRF at top_k=10, one GraphRetriever call at top_k=10 and max_hops=2, one expanded query through the same hybrid route when the deterministic B06 expansion produces a distinct query, and one final rerank when collected hits exist. GraphRetriever performs its existing query-to-EntityIndex seed lookup; absent seed candidates produce its actual empty result. The trace contains only actions that actually ran.
- FixedAgentPolicy does not apply sufficiency, no-gain, or other soft stops. Existing cancellation, deadline, max_steps, max_tool_calls, max_llm_calls, and max_total_tokens checks remain authoritative. An ordinary failed phase is retained as a failed ordered trace step and the next frozen phase may proceed. Fatal Generation/schema/input conflicts remain terminal.
- AgentSuiteRouteAdapter validates the returned fixed_agent trace against the only valid executed prefixes: hybrid; hybrid→graph; optionally expanded hybrid; optionally final rerank. It rejects reordered, duplicated, or extra actions. The suite registry already binds supported Agent profiles by exact route/config SHA, so no registry source change was needed; the test confirms fixed_agent loses only BLOCKED_B06_TRACE_PORT while BLOCKED_CACHE_CONTROL_PORT remains.

## Runtime assembly boundary

No application-level AgentServiceImpl construction appears under src/. The runtime that constructs AgentServiceImpl must provide the existing four-channel HybridRetriever as retrievers["hybrid"], the individual frozen BM25/Dense/Symbol/Entity/Graph retrievers required by the AgentService map, and the separate final reranker. The current service checks this component shape before any action. This branch does not invent or wire a production composition that is absent from the repository, and it did not run the formal suite.

## Remaining formal cache gate and isolation plan

| Component in fixed_agent | Current control | Required isolation/control before cold/warm formal runs |
| --- | --- | --- |
| Hybrid BM25 channel | Index files are read per query; OS/filesystem page cache is uncontrolled | Run cold queries in a disposable clean VM snapshot or provide a verified host page-cache reset. Use a stable isolated index root for both states; for warm, execute a frozen query warm-up and verify the same index/config SHA. |
| Hybrid Dense channel | Shared embedding-provider cache, Qdrant server cache, and KnowledgeStore/PG cache are uncontrolled | Use an isolated per-run embedding process and disposable Qdrant/PG services restored from the same frozen snapshot. Reset/restart or restore that snapshot for each cold query; reuse the restored services and frozen warm-up sequence for warm. A new collection alone does not prove Qdrant server cache is cold. |
| Hybrid Symbol and Entity channels | Index-file/OS page cache and KnowledgeStore/PG cache are uncontrolled | Include their exact index roots in the same disposable VM/service snapshot; verify reset/warm receipts for the index and store. |
| Following Graph step | GraphRetriever process-local snapshot cache is resettable/prewarmable; graph-store, EntityIndex, and KnowledgeStore caches are not verified | Keep the existing snapshot-cache receipt and add reset/warm participants for EntityIndex and graph/store backends, or run the whole dependency set from a verifiable clean snapshot. |
| Final reranker | Shared reranker-provider cache has no control receipt | Require a provider cache-reset API or a documented cache-disabled configuration with a matching SHA and observed receipt. A fresh client connection alone does not clear a remote shared cache. |
| Answer generation | Shared GenerationProvider cache is outside B04 control | Require a provider reset/disable capability and receipt. If the provider hides shared cache state, retain the blocker. |
| Agent composition | B04 sees the AgentSuiteRouteAdapter as unknown scope and cannot enumerate nested participants | Add an Agent cache-inventory capability that exposes the actual HybridRetriever channels, GraphRetriever, separate reranker, and shared answer provider. B04 must freeze this nested route manifest, sort the component IDs, execute each control, and require a complete receipt matching implementation and config SHA before dispatch. |

Host-level filesystem, Qdrant, PostgreSQL, and remote-provider cache state cannot be claimed cold from a new identifier or a cold/warm label. Where a verifiable reset operation is unavailable, use a disposable clean VM/service snapshot with an observed restore receipt or keep the formal suite blocked. This CR changes no cache gate and makes no cold/warm claim.

## Focused verification

- tests/b06/test_agent_fixed_policy.py exercises the actual HybridRetriever RRF composite over synthetic channel fixtures, verifies hybrid→graph→expanded-hybrid→rerank trace mapping, exercises the real GraphRetriever empty-seed path, asserts max_steps stopping, confirms a failed hybrid action is retained before Graph continues, checks fixed config SHA rejection before calls, and rejects missing hybrid/reranker components before calls.
- tests/b07/test_agent_trace_executor.py rejects a deliberately reordered fixed_agent result trace at the public adapter boundary.
- tests/b06/test_agent_trace.py and tests/b07/test_suite_registry.py cover the 14 distinct frozen Agent profiles and preservation of the B04 formal blocker.
- Focused B06/B07 regressions: 85 passed. Ruff and compileall passed. The tests use only synthetic in-memory inputs and local spies; no database, Qdrant, formal Test split, paid/external provider, or production path was touched.
- The implementation changes the Agent action contract and B06 Agent runtime only; it changes no frozen plan, quality threshold, Data source, storage code, cache controller, or run ledger.
