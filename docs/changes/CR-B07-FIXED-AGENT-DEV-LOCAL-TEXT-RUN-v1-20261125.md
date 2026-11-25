# CR-B07-FIXED-AGENT-DEV-LOCAL-TEXT-RUN-v1

## Status

Implemented and exercised with one self-authored synthetic Dev query. The query completed through the real local B03/B04/B05/B06 components and returned a citation-validated answer. This is not a formal B07 run and does not satisfy the formal cache-state gate.

## Scope

Exact changed files:

- `src/knowledge_weaver/evaluation/fixed_agent_dev.py`
- `tests/b07/test_fixed_agent_dev.py`
- `docs/changes/CR-B07-FIXED-AGENT-DEV-LOCAL-TEXT-RUN-v1-20261125.md`
- `docs/deliveries/B07-DENSE-DEV-REAL-20261125.md`

No B03/B04/B05/B06 source, storage schema, public port, frozen suite, threshold, formal cache gate, Data, database row, production configuration, or shared ledger was changed.

## Implementation

- Added `LoopbackB07ChannelConstructor`, which assembles the existing BM25, Dense, Symbol, and Entity retrievers against one `PostgresKnowledgeStore` and per-investigation `BudgetLedger`. Dense uses the loopback Qwen embedding provider and Qdrant client. The existing `GraphRetriever` uses `PostgresGraphStore` and the same KnowledgeStore.
- Added `DevOnlyTextRerankerFactory`. It hydrates each candidate through B03 `get_chunks`, tokenizes the actual persisted chunk text with the B04 BM25 tokenizer, and computes query-token coverage. Its manifest identifies the algorithm and configuration SHA, sets `dev_only=true` and `formal_eligible=false`, and does not claim a model artifact. The B06 budget ledger is attached to the reranker; the operation performs no model call.
- A text-reranker assembly has no `AgentSuiteRouteAdapter`. `run_fixed_agent_dev_query` accepts only an exact `BlindBenchmarkInput`, a verified Generation, context, and budget, then calls the public B06 `investigate_with_trace` method. Gold data is not available to this function. Runner-side synthetic measurements are computed after it returns.
- The local Dev evidence verifier uses a 90-second timeout and a 512-token output cap. Both settings and the verifier implementation are included in `component_config_sha256`. The strict response schema and evidence-support checks remain enabled. This local-only adjustment addresses the observed 20-second timeout and invalid JSON at the previous 256-token cap; it does not change formal B07 configuration.

## Local synthetic execution

Scope: one synthetic question, `What does acceptance_codename return?`, with `Language.en`; no frozen dataset, Test split, external service, or paid provider.

Persisted binding and available indexes:

- Corpus `9fe42200-d034-4989-9c08-cd755ad1c262`; Generation `30152134-f2d0-4395-bc0a-420a1b9439c2`, state `READY`, SHA-256 `99328dc402f975f851b292eadb099a21d98115b38708ccf6c1673f1dba3a085d`.
- B03 hydrated 4 chunks (2 leaf chunks), 6 evidence rows, 3 entities, and 3 relations. Both leaf chunk texts and hashes matched the existing BM25 index. The BM25, Symbol, and Entity index files were present; the two non-leaf parent chunks are not leaf retrieval candidates.
- The prior Qdrant collection `kw_9cb43fa41b679dd1b8ef88b5` was read only and contained the two expected Generation leaf points. The run used a new isolated collection, `b07dev_aecd7a2ca36c_9cb43fa41b679dd1b8ef88b5`, seeded from the PG-hydrated leaf text using Qwen3 embedding 0.6B at 1024 dimensions. It contained 2 points before and after query execution. B04's query-fingerprint payload update was limited to this new collection.
- PostgreSQL was reached at `127.0.0.1:55432`, Qdrant at `127.0.0.1:56333`, local embedding at `127.0.0.1:56336`, and local chat at `127.0.0.1:56337`. No query lease or other PostgreSQL write was issued.

Configuration identities from the completed run:

- Frozen `agent.fixed` route SHA: `c44d937a6374821a992e79e88cc7aec3bf19738443e42508cc5bfab2cd45cb53`.
- Component SHA: `9eda607127129c842154c01b5ad446abe246f2db6ac34f0dc525eefc931c7ae4`.
- Generation-provider SHA: `5bb370dfa3e3e56aacb59ced0338e0045306fe9f7e6f866273f316bacd9ec36a`.
- Dev text-reranker SHA: `d320fba31322713b6937572e89c726e37773daef8506dfefd569452360f3c254`; Dev verifier timeout/output cap: 90 seconds / 512 tokens.
- Local providers: `qwen3-embedding-0.6b` and `qwen3.5-4b-local`. Document embedding usage for the two seeded leaf chunks was exact: 37 input tokens, 0 output tokens.

Observed execution:

- B06 ordered trace: Hybrid → Graph → expanded Hybrid → rerank. Hybrid returned two leaf chunks; target chunk `b80f776d-38f2-5379-99a7-fac8dead54ee` ranked 1 and the other chunk ranked 2. Graph executed against the real PostgreSQL graph but returned 0 hits and 0 paths for this natural-language query. Verifier-supported evidence produced the expanded query suffix `LARKSPUR def str`.
- The local text reranker scored the target chunk `0.6` and the other chunk `0.0`, retaining the target at rank 1.
- Answer status was `answered`: “The function acceptance_codename returns the string \"LARKSPUR\".” It cited `22bca254-f7fa-5d7f-ab11-79125df8ee0f`, `90b3a272-748e-5d9b-a1d9-8c238b1e7512`, and `e1910a1e-354a-549c-8bf0-4bc809c63414`. All three were read back from B03 and validated; no invalid citations were returned.
- Runner-side synthetic retrieval measurements: target chunk rank 1, target evidence rank 1, recall@10 `1.0`, reciprocal rank `1.0`. These are Dev-only diagnostic measurements, not formal B07 scores or quality-threshold evidence.
- The local chat provider returned exact usage for the verifier call (1192 input / 267 output tokens) and answer call (1068 / 140). Aggregate B06 usage was marked `estimated`: 2282 input / 407 output tokens, 4 model calls including embedding, 5 tool calls, 73811 ms. The Qwen seed embedding usage is reported separately above.

The first verifier attempt exceeded its 20-second default. A direct retry with a 90-second timeout but 256 output tokens returned `StructuredResponseInvalid: local chat response content was not valid JSON`. After the Dev-only output cap was raised to 512 while preserving schema validation, the same blinded query completed successfully. No answer or citation was accepted from either failed attempt.

## Formal boundaries and warnings

- This Dev-only reranker cannot be registered through a B07 formal route adapter. No complete cache receipt was supplied; formal cache scope remains incomplete. This run does not mark any formal suite executable.
- Qdrant reported server `1.17.1` with client `1.20.0`, plus its existing warning about API-key use over loopback HTTP. The client/server versions and service configuration were not changed. No credential value was logged or persisted.

## Verification and Git

- Shared local environment command: `pytest tests/b07/test_fixed_agent_dev.py tests/b04/test_cache_state.py -q` → 14 passed. The restricted runner initially blocked pytest's Windows asyncio socketpair before test code; the approved local test environment was used. No dependency synchronization occurred.
- Ruff passed for the touched Python files; `compileall` and `git diff --check` passed.
- Code/test commit: `8956c8af0443bdb2b9ee36bec188917aa81304b9`, parent `0b82dc0aaba6a6348b39ecec92f311c31e4ef8a0`. Rollback ref `refs/codex/rollback/b07-fixed-agent-dev-text-20261125-001` points to the parent. Required baseline `1052ddcc75c09acf1199889df152bceaffab1915` remains an ancestor.
- The successful runtime used only the self-authored synthetic Generation. No formal Test, external provider, paid API, production data, Data workspace, or ledger was accessed.
