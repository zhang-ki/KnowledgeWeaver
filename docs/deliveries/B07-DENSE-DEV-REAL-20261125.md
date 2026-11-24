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
