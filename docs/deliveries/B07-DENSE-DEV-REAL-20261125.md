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
