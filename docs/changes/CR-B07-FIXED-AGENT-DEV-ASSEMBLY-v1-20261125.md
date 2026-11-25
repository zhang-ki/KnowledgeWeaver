# CR-B07-FIXED-AGENT-DEV-ASSEMBLY-v1

## Status

Implemented on the isolated `task/b07-blind-formal-executor` branch. The composition entry and closed preflight are ready; a real fixed_agent query remains blocked because this repository and local model directory have no local reranker implementation/artifact. No result was fabricated.

## Scope and construction contract

The change adds only `src/knowledge_weaver/evaluation/fixed_agent_dev.py` and its B07 construction regression. It composes the existing B04/B05/B06 interfaces without changing their public ports:

- A per-investigation channel factory builds the four typed B04 retrievers (BM25, Dense, Symbol, Entity) against the same `PostgresKnowledgeStore` and active `BudgetLedger`. Dense must use `QdrantDenseIndex` plus `LocalQwenEmbeddingProvider`; their configured loopback hosts and ports must match the checked Qdrant and embedding endpoints. The four channels are assembled into the existing `HybridRetriever` with RRF and no embedded reranker.
- The same local `PostgresKnowledgeStore` and a real `GraphRetriever` backed by `PostgresGraphStore` and `EntityIndex` are passed into `AgentServiceImpl`.
- The final reranker is a separate budget-aware `LocalDevRerankerFactory`. Its model file/directory is hashed; the returned implementation must implement the public `Reranker` port, expose matching local model identity, and remain the same implementation/config after preflight. Cloud and built-in fake providers are rejected.
- Generation is constructed per investigation budget through `DevLocalOpenAICompatibleGenerationProvider`, using the existing profile that only accepts an explicit `127.0.0.1` endpoint.
- Preflight requires loopback endpoints for PostgreSQL, Qdrant, query embedding, and chat, and probes each configured TCP port. It performs no SQL, model call, collection search, or retrieval. Constructing an actual `QdrantDenseIndex` can cause the Qdrant client library's default local version-compatibility handshake; the focused tests replace that client with a no-network stub.

The unchanged frozen `agent.fixed` behavior SHA identifies the protocol strategy. A separate `component_config_sha256` is emitted only when preflight succeeds. It binds implementation-source hashes and safe configuration for the actual KnowledgeStore, four channels, Hybrid RRF, GraphRetriever, verified local reranker artifact/config, local Generation profile, and configured loopback endpoints. Secret-shaped fields are omitted. Generation also returns a separate `generation_config_sha256` for the existing B07 executor contract.

## Local availability and run outcome

The repository contains `CloudReranker` as its only reranker implementation. `D:/agent/models` had no reranker or cross-encoder artifact. A fresh loopback-only preflight observed TCP listeners at `127.0.0.1:56333` (Qdrant), `:56336` (embedding), `:56337` (chat), and `:5432` (PostgreSQL). This checks only that TCP accepts a local connection; it does not establish service identity, API authorization, the selected isolated collection/database, or a Generation binding. The local Qdrant compatibility handshake reported client `1.20.0` against server `1.17.1`; no collection endpoint was accessed, and no dependency was changed.

The actual construction preflight was given no `KnowledgeStore`, channel retrievers, GraphRetriever, or local reranker factory. It returned `ready=false` and `component_config_sha256=null`; it named missing B03/B04/B05 objects and the absent local reranker. Since fixed_agent requires its separate reranker before taking any action, no synthetic query was sent through retrieval or generation. There is no B07 answer, ordered trace, citation result, usage, or score from this continuation.

## Cache control and remaining scope

The existing B04 `CacheStateController` can repeatably reset and prewarm the process-local `GraphRetriever` snapshot cache. `tests/b04/test_cache_state.py::test_graph_snapshot_cache_can_be_cleared_and_preheated_but_external_scope_stays_partial` verifies the observed warm/cold state and keeps `complete=false` because GraphStore, EntityIndex, and KnowledgeStore backend caches remain outside the receipt.

Other scopes still uncontrolled include OS/filesystem page cache for BM25/Symbol/Entity files; shared embedding-provider and Qdrant server cache; PostgreSQL/KnowledgeStore and GraphStore caches; and any shared reranker or answer-generation cache. A fresh collection, client, process, or cache label does not clear those scopes. The Agent adapter still has no frozen nested cache inventory. This CR changes no B04 cache receipt, no formal cold/warm gate, and no suite eligibility. Formal cold/warm needs verified component reset/restore receipts or isolated clean backend snapshots before it can be claimed.

## Verification

- `python -m pytest tests/b07/test_fixed_agent_dev.py tests/b04/test_cache_state.py -q`: 10 passed. Tests verify closed preflight, rejection of cloud/fake rerankers and non-loopback destinations, actual component-config SHA variation, budget-bound four-channel/Graph route wiring, and repeatable process-local Graph reset/prewarm with incomplete external scope. The test reranker raises if invoked; no test query or model output is generated. Final tests use a no-network Qdrant client stub.
- Ruff passed for the new evaluation module and B07 test. `compileall` passed for both. `git diff --check` passed.
- No formal Test, paid/external provider request, database query/write, Qdrant collection query/write, existing collection use, Data change, production configuration change, dependency synchronization, or ledger edit occurred. One initial construction regression created a default Qdrant client and its library performed only a loopback version-compatibility GET; later tests stub that client. No credential was read or printed.

## Exact files

- `src/knowledge_weaver/evaluation/fixed_agent_dev.py`
- `tests/b07/test_fixed_agent_dev.py`
- `docs/changes/CR-B07-FIXED-AGENT-DEV-ASSEMBLY-v1-20261125.md`
- `docs/deliveries/B07-DENSE-DEV-REAL-20261125.md` (delivery record)
