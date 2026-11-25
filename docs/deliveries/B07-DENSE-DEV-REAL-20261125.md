# B07 Dense Dev 本地真实检索切片 — 2026-11-25

## 范围与结论

本记录只覆盖自写合成文本上的本地 Dense 检索，不是正式矩阵结果。真实 Qwen 向量通过现有 `QdrantDenseIndex` 写入新集合，并由 `DenseRetriever` 和 `PublicPortBenchmarkExecutor` 实际查询；目标合成段落在 Top-K 排名第一。

回答生成未完成：本机没有确认可用的聊天 `GenerationProvider`。盲测 executor 到达生成端口后以 `PROVIDER_UNAVAILABLE`、不可重试失败。没有生成答案、Gold 分数或正式质量结论，也没有调用 DeepSeek/付费 provider。

## 本机服务

| 服务 | 真实状态 | 用途 |
|---|---|---|
| Qwen embedding | `http://127.0.0.1:56336/v1`，PID `28360`；`/health` 为 200；`/v1/models` 报告 `qwen3-embedding-0.6b`、1024 维 | 唯一本地向量生成端口 |
| Qdrant | `http://127.0.0.1:56333`，容器 `knowledgeweaver-qdrant-1`，版本 `1.17.1`；API key 认证开启 | 新集合写入与 Top-K |

Qwen 服务启动健康回执：`D:/agent/KnowledgeWeaver/.kw/runtime/verification/B07-Qwen-Dense-Dev-20261125-01.json`（用户提供；本 Worktree 未修改该文件）。

按要求未访问 `56334`。Qdrant key 仅从容器配置读入本次子进程环境并通过 `api-key` 请求头使用；没有打印、记录或写入密钥。默认匿名 `GET /collections` 返回 HTTP 401；认证后返回 200。验证前看到 24 个已有集合，本次没有改动它们。

运行环境发出兼容性警告：共享 Python 环境的 `qdrant-client 1.20.0` 对 Qdrant server `1.17.1` 的版本差距超过客户端支持范围。此处本次 REST 写入、查询和读回均成功；仍应由 B03/Orchestrator 处理版本对齐，不把警告隐藏。

## 隔离集合与实际结果

- 新集合：`b07dense20261125_1beb658365c726010be1c1f0`（由新 UUID corpus ID 导出；创建前确认不存在）。集合保留供后续检查。
- `corpus_id`: `9b6faf27-c1ce-4c47-a1ac-81481a910fb1`
- `generation_id`: `dfe2b9e3-4f70-4551-a497-45988d0e381e`
- 合成 `source_id`: `d6f11bc8-687d-4e42-9c26-b027e164ef0d`
- 合成问题：“What is the codename used in the B07 synthetic Dense Dev source?”；目标文本只声明合成代号 `LARKSPUR`，另有两条无关合成段落作为干扰项。
- 真正写入并读回 3 个 Qdrant 点，向量维度均为 1024；读回向量 L2 norm 范围为 `0.9999999899–1.0000000304`。
- 实际文档/查询 embedding 分别为 1024 维，norm 为 `0.9999999936` / `0.9999999891`；model 与 revision 均为 `qwen3-embedding-0.6b`。
- 读回 payload 的 `source_id`、`generation_id`、`chunk_id`、`embedding_mode=document`、声明的 `document/query` 模式对、模型 revision 和 dimension 均匹配；查询后的 `query_embedding_mode=query` 已写回，文档/查询 configuration fingerprint 相等。
- 现有 Dense adapter 记录的是 `generation_id`、embedding model revision 和 dimension，没有单独的 `source_version_id` payload 字段。因此本次验证的是来源 ID、索引 Generation 版本和模型 revision；没有声称 Qdrant 点本身保存了单独的 SourceVersion ID。
- 实际 `DenseRetriever` Top-K chunk IDs 顺序：
  1. `7245c73d-9ad5-4071-afce-4439401df11c` — 目标段落，rank 1
  2. `13705744-acd3-4380-8d8b-bfbaadb2941d`
  3. `5c26a760-9621-4595-9e08-6e67c15beae3`
- Dense `RetrievalResult` usage：1 次 tool call、391 ms、0 次 LLM call；这是检索端 usage，不含 embedding token 计量。此次脱敏回执没有保留 embedding provider 返回的 token usage 字段。

随后以 immutable `BlindBenchmarkInput`（只有 `query_id`、`question`、`language`）调用现有 `PublicPortBenchmarkExecutor`，绑定同一隔离集合及 `dev/dense/standard/seed-17` 单条 Dev route。检索再次返回目标 rank 1；本次检索延迟 579 ms。无本地聊天 provider 时，executor 返回不可重试 `PROVIDER_UNAVAILABLE`。回答为 `null`，Gold scoring 为 `not_run`。该单条合成 route probe 不是完整 `run_dense_dev_slice`、完整冻结 registry 或 B07 正式结果。

## 定向回归

执行命令：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location).Path 'src')
& 'D:/agent/KnowledgeWeaver/.venv/Scripts/python.exe' -m pytest -q `
  tests/b07/test_dense_dev_executor.py `
  tests/b07/test_final_pipeline.py::test_legacy_executor_branch_is_display_only_even_with_real_quality_shaped_output `
  tests/b07/test_final_pipeline.py::test_runner_does_not_retry_configuration_failures `
  tests/b07/test_final_pipeline.py::test_formal_runner_rejects_executor_supplied_gold_measurements
```

结果：8 passed，3.55 s。以上 pytest 使用合成/fixture 输入，只作为盲测执行、失败重试和矩阵复算代码回归，不作为真实运行证据。质量阈值和正式门禁未改。

## 尚缺端口与所有权

1. **B04 缓存控制，B04 owner。** 在 B04 retrieval 边界提供不可变 `CacheProfile`/cache-control port：冷态重置实际覆盖的索引与 store cache；暖态执行冻结 warm-up 序列；返回实际状态和 implementation/config SHA 回执；不支持或只应用一部分时在结果可用前 fail closed。现有请求标签不能证明 cold/warm 已执行。细节见 `docs/changes/CR-B07-B04-CACHE-STATE-PORT-v1-20261125.md`。
2. **B06 Agent trace，B06 owner。** 增加 `AgentService.investigate_with_trace(request, execution_profile) -> AgentExecutionResult`：返回回答、顺序 retrieval hits/paths、逐步与汇总 provider usage、实际 Generation 身份、route/config/weights SHA；profile 必须冻结 allowlist 并在 provider 调用前拒绝 ID/config/weights 不匹配。细节见 `docs/changes/CR-B07-B06-AGENT-TRACE-PORT-v1-20261125.md`。
3. **本地回答生成，运行配置/GenerationProvider owner。** 提供可确认的本地聊天 `GenerationProvider`、model/revision 与配置 SHA，再用合成 Dev 输入重跑 executor/runner；在此之前不得把 retrieval-only 结果计作回答或评分。

## Git 与范围

- 代码起点：`e50ab8e74ae3bdc0915f11b5335f54623aec89b7`，分支 `task/b07-blind-formal-executor`。
- 本次文档记录前的回滚 ref：`refs/codex/rollback/b07-dense-runtime-attempt-002`。
- 本次只修改本交付文档；未改源码、测试、公共端口、数据、数据库、其他 worktree 或运行账本。

## Continuation: local chat and runner-side synthetic Dev score — 2026-11-25

### Scope and path

- Added an evaluation-only `DevLocalOpenAICompatibleGenerationProvider`, restricted to an explicit `http://127.0.0.1:<port>/v1/chat/completions` endpoint. It sends no Authorization header, uses the existing provider budget ledger, asks for strict OpenAI-compatible JSON Schema output, and cannot fall back to a remote provider. The shared cloud provider/profile and external-authentication rules were not changed.
- Executed one synthetic query through `DenseRetriever` and `PublicPortBenchmarkExecutor` with the registered `dev/dense/standard/seed-17` route. The CandidateIndex performed Qwen query embedding and Qdrant `query_points` only. Before/after collection snapshots matched; no `set_payload`, upsert, or delete was called.
- Qdrant stores the three vector payloads and chunk IDs, not source text. For this Dev-only run, chunk text and citation records were hydrated from a self-authored in-memory synthetic fixture keyed by those stored chunk IDs. That fixture is not evidence that its text bytes exactly match the original text used to create the persisted vectors. No PostgreSQL or existing KnowledgeStore collection was read or modified.
- The runner-side `compute_query_measurements` function scored the returned Dense ranks against a self-authored synthetic Gold mapping. The dataset manifest is still `BLOCKED_INPUTS`; this was not `run_dense_dev_slice` over a frozen dataset and is not a formal or complete B07 result.

### Local runtime and measured output

| Item | Observed result |
|---|---|
| Query | “What is the codename used in the B07 synthetic Dense Dev source?” |
| Embedding | `qwen3-embedding-0.6b`, revision `qwen3-embedding-0.6b`, 1024 dimensions, exact usage 17 input tokens |
| Retrieval | Three stored points; target chunk `7245c73d-9ad5-4071-afce-4439401df11c` ranked 1. Remaining order: `13705744-acd3-4380-8d8b-bfbaadb2941d`, `5c26a760-9621-4595-9e08-6e67c15beae3` |
| Generation | Local `qwen3.5-4b-local` at `http://127.0.0.1:56337/v1/chat/completions`; exact usage 509 input / 35 output tokens; elapsed executor time 5,509 ms |
| Answer | `The codename used in the B07 synthetic Dense Dev source is LARKSPUR.` The model returned an empty `evidence_ids` list. The answer is real local model output, but citation validity is unavailable and no human answer review was done. |
| Runner metrics | Evidence recall 1.0; reciprocal rank 1.0; nDCG@5/10/20 1.0; recall@5/10/20 1.0; precision@5/10/20 0.20 / 0.10 / 0.05; strict chunk recall 1.0. Citation validity, citation precision, and answer correctness remain null. |
| Qdrant mutation check | Three points before and after; payload snapshot SHA-256 `46ffd8cc9ab8d78336b537dd3debe71d892513a42d9555eb57193d9891ad35bb`; unchanged |

The local Qwen chat artifact was `D:/agent/models/qwen3.5-4b/Qwen3.5-4B-Q4_K_M.gguf`, 2,740,937,888 bytes, SHA-256 `00FE7986FF5F6B463E62455821146049DB6F9313603938A70800D1FB69EF11A4`. Generation profile config SHA-256: `1da9fbf6bb141e5a74ba9567d58e6011e0ded08dc62ee2036f8e52d79c46ebf3`; Dense route config SHA-256: `94ed73db130031346065fcb4d6b5c0fe7066c81e0fa5efe1be9aedea0c80bcad`. The run-local suite registry SHA is `a41e3f79133c6f87a3ac2c9764a8247471c805a5b92dae24f99529e629a72324`; its input fixture was synthetic. The Generation digest `a75221d055cfa85147446a9a033d59825fb0c48ece91871a0767d25e740a7be8` was reconstructed only in memory for this synthetic invocation; no persisted Generation manifest or SourceVersion ID was verified from Qdrant.

No API key, Qwen cloud credential, external request, formal Test split, paid provider, or database write was used. The Qdrant client still warns that version `1.20.0` is outside its declared compatibility range for server `1.17.1`; this warning is retained.

### Focused verification and Git

- Exact changed paths for this continuation: `src/knowledge_weaver/evaluation/local_generation.py`, `tests/b07/test_local_generation.py`, and this delivery record.
- Focused local-provider plus Dense-executor regressions: 11 passed. Ruff on the two changed Python files and `git diff --check`: all checks passed. Tests used synthetic fixtures; the local runtime output above is separate live Dev evidence.
- Base/rollback: code base `8dbfa0f1e76b7ddfb816473937e5be48d1883e08`; `refs/codex/rollback/b07-local-chat-acceptance-attempt-001` points to that commit. The required baseline `1052ddcc75c09acf1199889df152bceaffab1915` remains an ancestor.
- Implementation candidate: `4d0997e5970126a2194e9010a641dcc116d013a6` (parent `8dbfa0f1e76b7ddfb816473937e5be48d1883e08`); the rollback ref above preserves the pre-change state.
- The local answer resolves the generation-unavailable blocker for this one synthetic Dense Dev route only. Missing frozen dataset manifest, persisted source text/SourceVersion binding, citation-bearing answer, B04 cache control, and B06 ordered trace remain open. No formal gate or quality threshold was relaxed.

## Continuation: PostgreSQL-backed Dense Dev synthetic acceptance — 2026-11-25

This run replaces the earlier in-memory hydration attempt as evidence for one isolated synthetic route. It does not turn the blocked dataset into a frozen dataset or claim a complete B07 Dev slice.

### Isolated persisted source and identity

- Evidence artifact: `.kw/runtime/verification/B07-PG-Dense-Dev-20261125-ef46ea39/result.json`; the self-authored source remains under the same run directory at `source/synthetic_acceptance.py`.
- The run created one new corpus `9fe42200-d034-4989-9c08-cd755ad1c262`, source `e02b21b0-690b-4bde-8fdc-5f6b18a8543c`, SourceVersion `1f47817e-f870-5b00-8b46-60b9527fffc5`, Document `5580fe23-ce56-5d3e-99c3-1f9ca51f9916`, and READY Generation `30152134-f2d0-4395-bc0a-420a1b9439c2`.
- The SourceVersion manifest SHA is `c67b72d583405bd8239a37326b5173cf4aeb272e5de5f74635462a10d90c0925`; the verified Generation SHA is `99328dc402f975f851b292eadb099a21d98115b38708ccf6c1673f1dba3a085d`. The Generation's sole SourceVersion ID is the ID above.
- Source-file SHA-256 and persisted Document `raw_hash` both equal `c01a16693ce761ede056cbd1fed1c605530a28690b15cfa120f4e93e8b3d338b`.
- The actual document embedding inputs had SHA-256 values `02d8fe72ae49ab52db48d672c4bc048ccbe48551c49130f778cfcfa33b172efc` and `df105aed5c5a0a80fd658407e249c2b4554f739d113b79bda637c16213509ac9`. This set exactly matches the persisted leaf Chunk text hashes. The target Chunk `b80f776d-38f2-5379-99a7-fac8dead54ee` was hydrated from PostgreSQL with SHA `02d8fe72ae49ab52db48d672c4bc048ccbe48551c49130f778cfcfa33b172efc`, equal to its hash at document-embedding time.
- Qdrant collection `kw_9cb43fa41b679dd1b8ef88b5` contains 2 points for this Generation. Its target point maps to the persisted target Chunk. Dense metadata records Qwen `qwen3-embedding-0.6b`, 1024 dimensions, and configuration fingerprint `faf94a9c69ffa8f099d5c7f549bc064ccf4b578d62a6aac7eb63a499dac938d4`.

### Blinded execution, answer, and runner measurement

- A `BlindBenchmarkInput` containing only the self-written query ID/question/language entered `PublicPortBenchmarkExecutor`. The real `DenseRetriever` used the new Qdrant collection, then hydrated Chunk and Evidence records through `PostgresKnowledgeStore`.
- Actual Top-K: target Chunk `b80f776d-38f2-5379-99a7-fac8dead54ee` at rank 1 (score `0.7758223`); `6dfc640b-ac07-5637-9985-139dbfc159b5` at rank 2 (score `0.5139288`).
- Local Qwen generated: “The synthetic codename returned by acceptance_codename in the persisted B07 source is 'LARKSPUR'.” The claim cites persisted Evidence `22bca254-f7fa-5d7f-ab11-79125df8ee0f`; PostgreSQL `validate_citations` returned that ID as valid with no invalid IDs.
- Provider usage was exact: embedding used 37 document-input tokens and 18 query-input tokens; chat used 547 input and 74 output tokens, one LLM call, and 21,209 ms executor elapsed time. Retrieval strategy latency was 406 ms. Chat artifact SHA-256: `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4`; generation config SHA: `973c16a69971a3e236262a515f02a94c6ca83d94069b9aa9bb03a924d2db8623`; Dense route config SHA: `0427ed38ca82082b5eabf1268d130784120263fd3087b8f4a4fcba914bc6aaa5`; one-route synthetic registry SHA: `3d548b73d91ab613beb95b51a3c0f17a5251947f23b4a4e309cec9e9f38e7023`.
- Runner-side `compute_query_measurements` scored this one self-authored diagnostic mapping against actual persisted retrieval output: evidence recall/MRR/nDCG/recall/strict chunk recall were `1.0`; precision@5/10/20 was `0.20/0.10/0.05`; citation validity was `1.0`. Citation precision and answer correctness remain `null` because this synthetic case has no human review. This is not a frozen Gold mapping, and `run_dense_dev_slice` was not run because the repository dataset manifest remains `BLOCKED_INPUTS`.
- The result is Dev-only. `cache_state="cold"` is a label and was not enforced. No formal Test split, sensitive input, external request, or paid provider was used. Qdrant emitted and we retained the warnings that the API key is being used over HTTP and qdrant-client `1.20.0` is outside the supported compatibility range for server `1.17.1`.
- The execution harness wrote the complete result artifact but its wrapper reported exit code `1` after output; that exit cause was not retained. A separate read-only recheck exited `0` and independently verified the persisted Generation digest, SourceVersion/source binding, source and document hashes, embedded-input/Chunk hash equality, Qdrant target point, Evidence-to-Chunk quote match, and PostgreSQL citation validity. No second generation request was made for the exit-status check.

### Failure found in an isolated Markdown probe

- A prior attempt with a Markdown source created only its own corpus `dba0404b-9d44-47cf-b9c4-05dbe7634114`, source `83fbaaa9-9ca5-40a6-b3eb-ea4f4d78633f`, Generation `3a5aaf48-d7c2-4b10-b349-82b920536946`, and collection `kw_774c211ed558c99c684816fa` (2 points). No query or answer was run against it.
- `verify_generation_manifest` rejected this Generation because the persisted `member_hashes` omitted the empty `entity` and `relation` member-set SHA values, while the Generation exposes those empty member lists. The B03 pipeline emitted the Generation as READY but did not materialize empty-set hashes for absent member kinds. This Work did not alter B03 source or patch the persisted record; it retained the failed isolated artifact and switched the successful synthetic route to a Python source that produces nonempty static Entity/Relation memberships. B03 should normalize all member kinds before hashing and add an empty-members regression.

### B04 cache-state handoff

- No B04-owned source or public port was changed. The existing [B04 cache-state CR](../changes/CR-B07-B04-CACHE-STATE-PORT-v1-20261125.md) specifies the minimum cross-batch contract: a cache profile for requested cold/warm state, a verifiable applied-state receipt bound to implementation/config SHA, cold reset and frozen warm-up behavior across the selected retrieval/store caches, and fail-closed behavior when control is unsupported or partial. Its acceptance tests preserve the frozen matrix and thresholds. Until B04 implements and integrates this port, formal cold/warm suites remain blocked.

### Code change, regression, and Git

- Exact tracked paths changed in this continuation: `src/knowledge_weaver/evaluation/local_generation.py`, `tests/b07/test_local_generation.py`, and this delivery document.
- The local-only answer provider now strengthens each claim's `evidence_ids` schema to `minItems: 1` without mutating the caller schema, instructs the model to copy a retrieved ID, and fails closed on `status=answered` with no cited claim. It changes no quality threshold and does not assign or fabricate citation IDs.
- Focused B07 regressions: 16 passed in 2.39 s across `test_local_generation.py`, `test_dense_dev_executor.py`, and the configuration-failure retry / executor-supplied-Gold rejection cases in `test_final_pipeline.py`. Ruff and `git diff --check` passed. Tests used synthetic fixtures; live local-model and persisted-store evidence is recorded above.
- Pytest ran with a temporary per-process `WindowsSelectorEventLoopPolicy`; the default Proactor loop had hung while creating its local self-pipe in the restricted runner. No shared runtime configuration or dependency was changed.
- Code commit: `1fe20196a89625aae1e012713960ff386bab086b` (parent `c9f4bfa43a2748c2dfe55a01223e91d642e17f00`). Rollback ref `refs/codex/rollback/b07-pg-dense-citation-schema-20261125-001` points to that parent. The required baseline `1052ddcc75c09acf1199889df152bceaffab1915` remains an ancestor.
- No formal gate or threshold was relaxed. The remaining formal blockers are the frozen dataset, B03 empty-member hash consistency for text-only sources, B04 cache-state control, and B06 ordered Agent trace.

## Continuation: scoped B04 cache-state control — 2026-11-25

### Scope and real cache inventory

- Continued from `0abb45029f0a8241dcfebbfc82a5751e9e5d94a4` on `task/b07-blind-formal-executor`; the required baseline remains an ancestor.
- The only current B04 process-local retrieval result cache is `GraphRetriever._snapshot_cache`, an LRU-like bounded `OrderedDict` keyed by corpus, Generation, source filter, and `include_inferred`. B04 now exposes an idempotent clear operation, a warm operation that loads the graph view for the current `QueryContext`, and a check that the same key is present afterward.
- Dense has no controllable B04 result cache; its query path uses shared Embedding Provider state and Qdrant server state, then hydrates from the KnowledgeStore. BM25/Symbol/Entity read their index files each search but cannot clear the OS page cache; they also use the KnowledgeStore backend. Hybrid unions all channel scopes and adds an optional reranker shared-provider cache. The evaluator also lists the shared answer GenerationProvider cache as uncontrolled because end-to-end latency includes generation. These external/shared caches are reported as uncontrolled.
- The frozen warm-up order is ascending component ID. Graph currently has one controlled step, `retriever.graph/graph-snapshot-cache`; its receipt explicitly lists graph-store, entity-index, and KnowledgeStore cache scope that remains unverified.

### Evaluator behavior and fail-closed gate

- `CacheStateController` applies and verifies each declared operation, computes an implementation SHA and per-route config SHA, and returns an immutable receipt with requested/applied state, controlled/uncontrolled components, fixed warm-up order, and operation results.
- `PublicPortBenchmarkExecutor` derives cache controls from the route-bound retriever inventory; callers cannot inject an unrelated all-clear controller. It validates route, requested state, and config SHA before retrieval. Incomplete scope fails with `CACHE_SCOPE_INCOMPLETE` before the measured `Retriever.retrieve` call. Missing receipts and mismatched hashes fail at the same boundary. `AttemptOutcome` cannot be complete without a complete matching receipt; failed attempts retain any partial receipt. Dense Dev `cache_state_enforced` is derived from every attempt rather than a default label.
- No current Dense/BM25/Symbol/Entity/Hybrid route can claim complete cold/warm control through the repository's existing adapters. The formal suite registry blocker remains in force. This change does not lower the formal gate or change any quality threshold.
- No database, Qdrant, provider, Test split, or paid inference was used for these changes. The tests use isolated memory fixtures; the real Graph controller is exercised against a memory GraphStore.

### Focused verification and Git

- Exact tracked scope: `src/knowledge_weaver/retrieval/cache_state.py`, `src/knowledge_weaver/retrieval/__init__.py`, `src/knowledge_weaver/graph/retriever.py`, `src/knowledge_weaver/evaluation/executors.py`, `src/knowledge_weaver/evaluation/runs.py`, `src/knowledge_weaver/evaluation/runner.py`, `src/knowledge_weaver/evaluation/suite_registry.py`, `tests/b04/test_cache_state.py`, `tests/b07/test_dense_dev_executor.py`, `tests/b07/test_generation_binding.py`, `tests/b07/test_final_pipeline.py`, `tests/b07/test_suite_registry.py`, this delivery record, and `docs/changes/CR-B07-B04-CACHE-STATE-PORT-v1-20261125.md`.
- Targeted regressions cover cold reset repeatability, fixed warm-up order, actual Graph snapshot cache reset/prewarm, incomplete scope, missing receipts, wrong config hash, retry receipt retention, complete-attempt receipt requirement, and preservation of the formal suite registry blocker. Final run: 66 passed in 3.80 s; Ruff and `git diff --check` passed. Shared-venv pytest used the per-process Windows Selector event-loop policy; no dependency was synchronized.

## Continuation: B03 empty Entity/Relation member hashes — 2026-11-25

### Contract and minimal change

- Continued from `e133b5d761b2f81cc8412356c6619016500fe27b` on `task/b07-blind-formal-executor`; required baseline `1052ddcc75c09acf1199889df152bceaffab1915` remains an ancestor.
- Before changing the publisher, verified the existing B07 verifier contract: `SHA256(b"")` is `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`. A present `entity`/`relation` key with this value verifies an empty membership set; an absent key is rejected. The verifier and its requirements were not relaxed.
- The B03 production publisher now hashes an explicit empty set for `entity` and `relation` at both Generation publication sites (source ingest and source deletion). It leaves the membership rows untouched and does not mutate the caller's member mapping. No failed Generation, existing corpus pointer, or B04/B06 port was changed.
- Regressions cover canonical empty hashes and non-mutation in B03, verifier acceptance of explicit empty hashes, and isolated rejection of each missing key in B07.

### Isolated real PG/Qdrant validation

- New self-authored Markdown source: `.kw/runtime/verification/B03-PG-Qdrant-Empty-Members-20261125-ed437d2b/source/empty-members.md`; source file SHA-256 `83b3d2d90136ab1c2837c859ba667ee4ba694b041807b8655a143156100dab64`.
- Created only new corpus `b4773007-1e1f-4af0-b800-a51933167c3e`, source `d080dd2b-121b-45cd-aeb6-367c88ee2720`, SourceVersion `06a553c8-9383-559f-be52-9ed83044d069`, Document `2e94fa27-e069-53d6-8de7-1a140a58bce5`, READY Generation `9022bb0d-293b-45f5-b81e-5efd2ead6335`, and Qdrant collection `kw_c54307cc4f1d0fcf34314395`.
- The persisted Generation has no Entity or Relation IDs; each corresponding member hash is the canonical empty SHA above. The formal `verify_generation_manifest` accepted the persisted Generation. Its Generation SHA-256 is `9732ff462f9495883a357f56a1fb95e879ef277fa508d96160c31ea1f7601bec`.
- PostgreSQL read-back verified two persisted Chunks and two Evidence records. Qwen `qwen3-embedding-0.6b` produced 1024-dimensional document embeddings for both chunks; Qdrant contains and verifies two points for this Generation in the new collection.
- Isolation checks passed: all pre-existing corpus active-Generation pointers were unchanged; the prior failed Generation `3a5aaf48-d7c2-4b10-b349-82b920536946` retained its original digest/state and still omits both empty-set keys; its Qdrant collection `kw_774c211ed558c99c684816fa` remained at 2 points. No old object was repaired in place.
- Raw one-line process stdout is preserved at `.kw/runtime/verification/B03-PG-Qdrant-Empty-Members-20261125-ed437d2b/stdout.txt` (`status=PASS`, canonical empty hash, IDs and isolation checks above; SHA-256 `93188601047f949bc2a19901d807e98f11d68ae3743a71c62a0fad8188ea0181`). The actual process exit code is preserved at the adjacent `exit_code.txt`: `0` (SHA-256 `13bf7b3039c63bf5a50491fa3cfd8eb4e699d1ba1436315ae9fcbe5711530354`). Full structured evidence is in `result.json` (SHA-256 `12334c7d567396cb2ee1f76e5d05aa9da110e427a8aed3f9c83e0de1317096fb`); local client compatibility warnings are preserved in `stderr.txt`.

### Focused verification and Git

- Targeted B03/B07 tests: `tests/b03/test_pipeline_service.py::test_generation_member_hashes_materialize_empty_entity_relation_without_mutating_members` and `tests/b07/test_generation_binding.py`; the final rerun passed 13 tests in 3.85 s. Ruff on the three changed Python files and `git diff --check` also passed.
- Allowed tracked scope for this continuation: `src/knowledge_weaver/storage/pipeline.py`, `tests/b03/test_pipeline_service.py`, `tests/b07/test_generation_binding.py`, and this delivery document only. No dependency synchronization, formal Test run, paid inference, B04/B06 interface change, database/Data edit, or shared-ledger edit was made.
- B03 fix commit: `5b501df2c3afcc35c620e7ccb0a5b6754dce7798`, parent `e133b5d761b2f81cc8412356c6619016500fe27b`; rollback ref `refs/codex/rollback/b07-b03-empty-member-hashes-20261125-001` points to the parent. The required baseline remains an ancestor and the worktree was clean after the commit.

## Continuation: B06 ordered Agent trace and B07 adapter — 2026-11-25

### Public interface and route binding

- Continued from `b0052ac434b054493bf70a142957616eb473f99f` on `task/b07-blind-formal-executor`; the required baseline `1052ddcc75c09acf1199889df152bceaffab1915` remains an ancestor.
- Added immutable `AgentExecutionProfile`, `AgentExecutionTraceStep`, `AgentProviderCallUsage`, and `AgentExecutionResult` DTOs plus the additive public `AgentService.investigate_with_trace(request, execution_profile)` method. Existing `investigate(request) -> Answer` remains callable and returns only the legacy Answer; it does not fabricate trace rows. The public contract and compatibility path are specified in [CR-B07-B06-AGENT-TRACE-PORT-v1](../changes/CR-B07-B06-AGENT-TRACE-PORT-v1-20261125.md).
- B06 records the retrieval action's actual returned hits and paths at execution time, plus its source/action, query hash, Generation context, warnings, measured usage, and observed failure. Actual provider calls retain order, purpose, provider model/revision, usage, and failures. No trace is reconstructed from the final Answer. B07 converts only `BlindBenchmarkInput` into an Agent request; Gold, review, and source filters remain runner-side or absent.
- The B07 registry recognizes 13 unique route/config bindings: rule, llm_router, segr, and SEGR ablations A01–A10. `fixed_agent` remains blocked because its frozen fixed hybrid/graph/expansion/rerank behavior is not the current B06 `FixedPolicy` behavior. SEGR and ablation routes require the exact configured policy weights SHA; all routes require the matching Generation-provider config SHA and verified Generation identity.
- The trace route blocker is removed only for an exact matching public adapter. The existing `BLOCKED_CACHE_CONTROL_PORT` remains for every formal suite. Cache scope remains incomplete for Dense, BM25, Symbol, Entity, Hybrid, Agent nested retrieval, and shared answer generation. Graph's process-local snapshot cache is the only currently resettable/warmable component; graph store, indexes, KnowledgeStore, Qdrant, embedding, reranker, OS page, and provider caches remain uncontrolled or unverified. Cache labels alone do not establish cold/warm state; Agent execution requires a complete matching B04 receipt and otherwise fails closed.

### Exact change scope and validation

- Exact code/test paths changed: `src/knowledge_weaver/agent/__init__.py`, `src/knowledge_weaver/agent/service.py`, `src/knowledge_weaver/agent/execution_profiles.py`, `src/knowledge_weaver/evaluation/agent_adapter.py`, `src/knowledge_weaver/evaluation/executors.py`, `src/knowledge_weaver/evaluation/runs.py`, `src/knowledge_weaver/evaluation/suite_registry.py`, `src/knowledge_weaver/models/contracts.py`, `src/knowledge_weaver/ports/__init__.py`, `tests/b06/test_agent_trace.py`, `tests/b07/test_agent_trace_executor.py`, and `tests/b07/test_suite_registry.py`; this delivery record and the B06 trace CR are the only documentation paths.
- Trace/service and route/cache-regression command covered `tests/b06/test_agent_trace.py`, `tests/b06/test_agent.py`, `tests/b07/test_agent_trace_executor.py`, `tests/b07/test_suite_registry.py`, and `tests/b07/test_dense_dev_executor.py`: 42 passed. Existing final-pipeline and Generation-binding regressions (`tests/b07/test_final_pipeline.py`, `tests/b07/test_generation_binding.py`): 35 passed. Ruff and compileall passed. These are focused synthetic/spy regressions, not formal evaluation results.
- No extraction or storage source changed; no database/Qdrant operation, formal Test run, paid/external provider request, dependency synchronization, or shared-ledger edit was made.
- Implementation commit: `55b0f5114b25e2bbe323c9622b75c2c01d78af8a`, parent `b0052ac434b054493bf70a142957616eb473f99f`. Rollback ref `refs/codex/rollback/b07-b06-agent-trace-20261125-001` points to that parent. Delivery-record follow-up rollback ref `refs/codex/rollback/b07-b06-agent-trace-delivery-20261125-001` points to the implementation commit. The required baseline remains an ancestor.

## Continuation: frozen fixed_agent route — 2026-11-25

### Frozen sequence and implementation

- Continued from `b878436a427877157fc4a41642b353c69e66e7a6` on `task/b07-blind-formal-executor`; required baseline `1052ddcc75c09acf1199889df152bceaffab1915` remains an ancestor.
- The frozen plan specifies `hybrid → graph → one expansion → rerank` under hard budget. The old B06 `FixedPolicy` selected separate BM25, Dense, Symbol, Entity, Graph, and Rerank actions and could not identify the HybridRetriever action. Added `ActionTool.hybrid` so the public ordered trace records the real composite action rather than relabeling a channel.
- Added `FixedAgentPolicy` and a route-bound candidate sequence. The service requires the actual four-channel B04 `HybridRetriever` (BM25/Dense/Symbol/Entity with RRF, no embedded rerank) and a separate final reranker before the first call. It executes hybrid at top_k=10, GraphRetriever at max_hops=2 (using its existing EntityIndex query lookup and real empty result if no seed resolves), one existing deterministic query expansion capped at three added terms through the same HybridRetriever, then rerank if there are hits. Soft stopping is disabled; max steps/tool calls/LLM calls/tokens/deadline remain in force.
- The `agent.fixed` config SHA binds the fixed phase sequence and its RRF/top_k/hops/expansion/soft-stop settings. B06 validates route/config identity before provider calls; B07 validates returned trace order. The suite registry clears only `BLOCKED_B06_TRACE_PORT` when the route adapter matches. `BLOCKED_CACHE_CONTROL_PORT` remains, so no formal suite becomes executable.
- `src/` contains no application-level `AgentServiceImpl` construction. The caller that builds the real service must inject `retrievers["hybrid"]` and the separate reranker. This branch does not fabricate a production composition or a formal run.

### Formal cache components and isolation path

- Hybrid BM25, Symbol, and Entity channels read index files, but OS/filesystem page caches and KnowledgeStore/PG cache are not reset or verified. Use a disposable clean VM/service snapshot for cold query state, retain the same isolated index/store snapshot for warm state, and record verified restore/warm receipts.
- Dense additionally depends on shared embedding-provider and Qdrant server caches. Use isolated embedding and Qdrant services restored from a frozen snapshot per cold query; a new collection is insufficient evidence of cold state. PostgreSQL/KnowledgeStore must be in the same resettable snapshot or separately expose and verify reset/warm operations.
- Graph's process-local snapshot cache can reset/prewarm; its graph-store, EntityIndex, and KnowledgeStore caches remain unverified. Add those to the nested Agent cache inventory or keep the route blocked.
- The separate reranker and answer GenerationProvider have no shared-cache control receipt. They need an explicit cache-reset API or a cache-disabled config with a matching SHA and receipt. A new client process cannot prove a remote shared cache is cold.
- B04 currently classifies the Agent route as unknown cache scope because it cannot inspect nested components. The implementable next interface is an Agent cache-inventory capability enumerating actual channels, GraphRetriever, reranker, and GenerationProvider; B04 then freezes and applies the complete component list in stable order. If any host or remote scope lacks a verifiable reset, formal cold/warm remains blocked. This continuation changed no B04 source or gate.

### Exact scope and verification

- Exact code/test scope: `src/knowledge_weaver/models/contracts.py`, `src/knowledge_weaver/agent/__init__.py`, `src/knowledge_weaver/agent/execution_profiles.py`, `src/knowledge_weaver/agent/policies.py`, `src/knowledge_weaver/agent/service.py`, `src/knowledge_weaver/evaluation/agent_adapter.py`, `tests/b06/test_agent_fixed_policy.py`, `tests/b06/test_agent_trace.py`, `tests/b07/test_agent_trace_executor.py`, and `tests/b07/test_suite_registry.py`. Documentation scope is the new `docs/changes/CR-B07-FIXED-AGENT-ROUTE-v1-20261125.md`, the updated `docs/changes/CR-B07-B06-AGENT-TRACE-PORT-v1-20261125.md`, and this delivery record only.
- Focused synthetic B06/B07 regressions: 85 passed in 3.69 s. Coverage includes ordered real HybridRetriever RRF results, real GraphRetriever empty-seed handling, expansion/rerank mapping, max_steps stop, failed hybrid action followed by graph, config SHA mismatch before calls, missing hybrid/reranker fail-closed checks, adapter rejection of reordered trace, and preservation of the formal B04 blocker. Ruff and compileall passed.
- The first system-Python pytest invocation lacked `pytest-asyncio` and skipped async cases; it was excluded. The restricted runner then blocked pytest's local event-loop socketpair before test code. The final run used the existing shared `D:/agent/KnowledgeWeaver/.venv` with local test-process permission and no dependency installation/synchronization. No Test dataset, DB, Qdrant, paid/external provider, production configuration, Data, storage source, or ledger was accessed or changed.
- Implementation commit: `56a3db54a533e4767144105ea893133b2faf8ace`, parent `b878436a427877157fc4a41642b353c69e66e7a6`. Rollback ref `refs/codex/rollback/b07-fixed-agent-route-20261125-001` points to that parent. Delivery-record follow-up rollback ref `refs/codex/rollback/b07-fixed-agent-delivery-20261125-001` points to the implementation commit. The required baseline remains an ancestor.

## Continuation: fixed_agent local Dev assembly preflight — 2026-11-25

- Continued from `daf0ffd560e1d86458a19a8fa78b9d525ae2a6d2` on `task/b07-blind-formal-executor`; the required baseline `1052ddcc75c09acf1199889df152bceaffab1915` remains an ancestor.
- Added `evaluation/fixed_agent_dev.py` as an evaluation-only composition boundary. It requires a per-investigation B04 channel factory so Dense's `LocalQwenEmbeddingProvider` shares the B06 budget ledger. It validates exact BM25/Dense/Symbol/Entity wrappers and backing index classes, a loopback-only QdrantDenseIndex and LocalQwen embedding endpoint, the same loopback PostgresKnowledgeStore/PostgresGraphStore, the Graph EntityIndex, a separate local reranker backed by a hashed model artifact, and the loopback chat profile. It constructs the existing HybridRetriever (RRF without an embedded reranker), GraphRetriever, AgentServiceImpl, local generation factory, and `agent.fixed` trace adapter. Per-investigation reranker identity is rechecked against the preflight descriptor.
- The full component digest binds actual implementation source hashes, safe configuration, verified reranker artifact SHA, frozen fixed_agent profile SHA, and the checked loopback endpoints. The frozen route SHA and Generation config SHA keep their existing meanings. No secrets are included in the manifest.
- Fresh TCP checks accepted `127.0.0.1:56333` (Qdrant), `:56336` (embedding), `:56337` (chat), and `:5432` (PostgreSQL). The repository still has only `CloudReranker`; no local reranker artifact was found in `D:/agent/models`. With no local reranker factory and no actual B03/B04/B05 objects passed to the runtime preflight, the report is `ready=false`, names those missing components, and returns no component SHA. The constructor fails closed before any query. No synthetic query was sent; this continuation has no retrieval hit, Agent trace, answer, citations, usage, or score.
- The Qdrant client version-compatibility handshake observed local server `1.17.1` while the shared environment has client `1.20.0`. This was a library version GET only; it did not access a collection. The final focused test run stubs the client to avoid service traffic. Dependency versions and the Qdrant service were not changed.
- Cache control remains separate from route construction. Existing `CacheStateController` tests prove repeated process-local Graph snapshot reset and warm-up; receipts remain incomplete because GraphStore, EntityIndex storage, and KnowledgeStore backend scopes are uncontrolled. OS/filesystem page cache, Qdrant shared cache, PostgreSQL cache, embedding provider cache, and shared reranker/generation cache are also uncontrolled. The formal B04 cache gate remains unchanged.
- Exact new source/test scope: `src/knowledge_weaver/evaluation/fixed_agent_dev.py`, `tests/b07/test_fixed_agent_dev.py`; CR: `docs/changes/CR-B07-FIXED-AGENT-DEV-ASSEMBLY-v1-20261125.md`; this delivery record. Existing `tests/b04/test_cache_state.py` was reused without edits.
- Focused validation in the shared `D:/agent/KnowledgeWeaver/.venv`: `pytest tests/b07/test_fixed_agent_dev.py tests/b04/test_cache_state.py -q` → 10 passed; Ruff, compileall, and `git diff --check` passed. Tests only construct route objects and verify preflight/config binding; the test reranker raises if called. No Test data, SQL read/write, Qdrant collection read/write, paid API, external provider, Data, production configuration, or ledger was touched.
- Implementation commit: `e2e29fed43b1836d58a486c0ba0ae34ebdd350f9`, parent `daf0ffd560e1d86458a19a8fa78b9d525ae2a6d2`. Rollback ref `refs/codex/rollback/b07-fixed-agent-dev-assembly-20261125-001` points to the parent. The delivery-record follow-up rollback ref `refs/codex/rollback/b07-fixed-agent-dev-delivery-20261125-001` points to this implementation commit.

## Continuation: real fixed_agent synthetic Dev slice — 2026-11-25

### Real assembly and formal boundary

- Continued from `0b82dc0aaba6a6348b39ecec92f311c31e4ef8a0` on `task/b07-blind-formal-executor`; required baseline `1052ddcc75c09acf1199889df152bceaffab1915` remains an ancestor.
- No local reranker model or weight files were present. Added a deterministic local text reranker that uses the existing B04 BM25 tokenizer and actual chunk text hydrated by B03. It is tagged `dev_only=true`, `formal_eligible=false`, has its own configuration SHA, and has no formal `AgentSuiteRouteAdapter`. It is not described as a model.
- Added a loopback B04 channel constructor for the actual BM25, Dense, Symbol, and Entity adapters. Dense used local Qwen embedding plus Qdrant. B05 used the real `GraphRetriever` and `PostgresGraphStore`; B06 used the public `investigate_with_trace` API. The one-query entry accepts only `BlindBenchmarkInput` and a verified Generation; Gold stayed in the runner after execution.
- The first local verifier attempt timed out at 20 seconds. With a 90-second timeout and the previous 256-token response cap it produced invalid JSON. The Dev-only verifier now allows 512 output tokens and retains schema validation. Its implementation, timeout, and output cap are covered by the component SHA. No formal verifier setting changed.

### Persisted Generation and isolated index

- Read-only B03 hydration confirmed Generation `30152134-f2d0-4395-bc0a-420a1b9439c2`, state `READY`, SHA-256 `99328dc402f975f851b292eadb099a21d98115b38708ccf6c1673f1dba3a085d`; 4 chunks (2 leaf), 6 evidence, 3 entities, and 3 relations. The two leaf chunk texts and hashes matched the existing BM25 index; BM25, Symbol, and Entity index files were present. The two parent chunks are intentionally absent from leaf retrieval indexes.
- The old collection `kw_9cb43fa41b679dd1b8ef88b5` was read only and contained the two Generation leaf points. A fresh collection, `b07dev_aecd7a2ca36c_9cb43fa41b679dd1b8ef88b5`, was seeded from PG-hydrated leaf text using Qwen3 embedding 0.6B, 1024 dimensions. It contained 2 points before and after the query. Query-fingerprint metadata writes were confined to this new synthetic collection. PostgreSQL received no writes or query lease.
- Live local endpoints were Postgres `127.0.0.1:55432`, Qdrant `127.0.0.1:56333`, embedding `127.0.0.1:56336`, and chat `127.0.0.1:56337`. Qdrant's version check warned that client `1.20.0` differs from server `1.17.1`; versions and service settings were not changed.

### One blinded query result

- Input: `What does acceptance_codename return?` (`en`). The B06 fixed-agent trace executed Hybrid → Graph → expanded Hybrid → rerank. Hybrid returned two hits; target chunk `b80f776d-38f2-5379-99a7-fac8dead54ee` ranked first. Graph executed against PostgreSQL and returned 0 hits / 0 paths for this query. Verifier-supported evidence generated the expanded suffix `LARKSPUR def str`. The Dev text reranker scored the target `0.6`, the other hit `0.0`, and kept the target at rank 1.
- The local chat model answered: “The function acceptance_codename returns the string `LARKSPUR`.” It cited evidence IDs `22bca254-f7fa-5d7f-ab11-79125df8ee0f`, `90b3a272-748e-5d9b-a1d9-8c238b1e7512`, and `e1910a1e-354a-549c-8bf0-4bc809c63414`; all three passed B03 citation validation and resolved to the persisted target chunk.
- Runner-side synthetic diagnostics: target chunk/evidence rank 1, recall@10 `1.0`, reciprocal rank `1.0`. These values are not formal suite scores and do not change quality thresholds.
- Local chat usage was exact per successful provider call: verifier 1192 input / 267 output tokens; answer 1068 / 140. Aggregate B06 usage was `estimated`: 2282 input / 407 output tokens, 4 model calls including embedding, 5 tool calls, 73811 ms. Seeding the two document vectors used 37 input / 0 output tokens, exact.
- Component SHA `9eda607127129c842154c01b5ad446abe246f2db6ac34f0dc525eefc931c7ae4`; Generation-provider SHA `5bb370dfa3e3e56aacb59ced0338e0045306fe9f7e6f866273f316bacd9ec36a`; reranker SHA `d320fba31322713b6937572e89c726e37773daef8506dfefd569452360f3c254`; frozen `agent.fixed` route SHA `c44d937a6374821a992e79e88cc7aec3bf19738443e42508cc5bfab2cd45cb53`.

### Verification and limits

- `pytest tests/b07/test_fixed_agent_dev.py tests/b04/test_cache_state.py -q` → 14 passed. Ruff, `compileall`, and `git diff --check` passed. Tests ran with the shared `D:/agent/KnowledgeWeaver/.venv`; no packages were installed or synchronized.
- Exact implementation scope: `src/knowledge_weaver/evaluation/fixed_agent_dev.py` and `tests/b07/test_fixed_agent_dev.py`. This CR and the present delivery record are the only documentation files for this continuation.
- The final execution was one self-authored synthetic Dev query, not a frozen suite or Test run. No external/paid provider, production data, Data workspace, or shared ledger was accessed. The formal cache receipt remains incomplete; this Dev result does not make a formal suite executable.
- Code/test commit: `8956c8af0443bdb2b9ee36bec188917aa81304b9`, parent `0b82dc0aaba6a6348b39ecec92f311c31e4ef8a0`. Rollback ref `refs/codex/rollback/b07-fixed-agent-dev-text-20261125-001` points to that parent.
- Detailed component contract and evidence: [CR-B07-FIXED-AGENT-DEV-LOCAL-TEXT-RUN-v1](../changes/CR-B07-FIXED-AGENT-DEV-LOCAL-TEXT-RUN-v1-20261125.md).
