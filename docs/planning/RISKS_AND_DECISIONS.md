# 风险、固定决策与待确认项

| 事项 | 固定处理 |
|---|---|
| 新项目目录 | D:\agent\KnowledgeWeaver；本轮检查为空且非Git，不查找/恢复删除的项目 |
| 算法深度 | 只围绕SEGR收益预测/反馈/成本决策，低维ridge，避免堆MCTS/RL |
| 语义奖励失真 | 合法性和支持度分离；train人工Gold/test代理；最终人工答案评审 |
| 文档复杂布局 | Docling普通文本PDF/表格；OCR/复杂公式非MVP；保留warn，不假装完美 |
| 费用 | 未选provider；Fake可离线开发；真实运行需要金额/数据外发授权；全矩阵先估算 |
| Windows依赖 | Python3.11独立venv，Docling/模型CPU性能P1/P3实测；不迁WSL |
| Docker基线 | 精确候选tag，P1核查安全与可用性并锁digest；运行健康未验证 |
| 跨库一致性 | generation先构建/验证再发布，单corpus writer与租约 |
| 协作能力不成立 | P2必须实测；工具缺失报告替代/CR，不能伪造全自动 |
| Git集成失败 | 候选merge后测试；失败integration不动；已有后继不用硬回退 |
| 调度提前结束 | 持久账本+drain七项检查+已验证周期唤醒、报告持久化和Orchestrator恢复轮询；等待状态不称完成 |
| 历史任务误通知 | 全新instance_id、显式Orchestrator绑定、旧版本消息拒绝；不恢复旧代码 |
| 人工审查瓶颈 | 在B07.prep提前准备；缺Gold审校只阻塞对应gate，不假签审查 |

需要用户在对应阶段确认：

1. P0：当前plan_hash架构审查批准与审查人身份；当前状态仅READY_FOR_REVIEW。
2. P1：安装/配置Python3.11、uv、Docker Desktop的授权；Windows/虚拟化/磁盘可用性、Docker许可适用性；密码由本地生成/配置。
3. P1/P3：云LLM/embedding/reranker候选endpoint与model、允许外发哪些资料、单次与实验总预算、价格快照；本轮不收集Key。
4. P2：实际Orchestrator会话和本地project绑定、创建probe会话/提交/集成的执行授权已由用户授权；监控automation为ACTIVE、每15分钟，按用户决定保持配置不变，独立monitor thread `01a1efc6-349f-7b92-83a0-c1c242524176`。持久化报告轮询规则见下方决议。
5. B07：合法语料与人工Gold/答案评审人员，正式矩阵估算费用授权；不足时按CR调整范围。
6. RELEASE：可选GitHub账户/可见性/发布授权，缺失不阻塞本地开发与本地演示。

当前阻塞是后续动作尚未授权/验证，不阻塞Phase0文档交付。安装、系统级配置、远端创建、收费调用、不可逆删除不随文档生成自动发生。

## 增补与决议记录

### KW-DEC-ORCH-REPORT-POLL-20261119 — 2026-11-19

- 状态：本记录中的90分钟周期仅保留为此前请求/协议文字的历史背景，已由 `KW-USER-DECISION-P2-MONITOR-CADENCE-20261119` 取代；它不是当前配置，也不表示曾验证过90分钟周期。其余持久化和轮询规则继续有效。

- 协议增补版本：`KW-ORCH-REPORT-POLL-2026-11-19-v1`。冻结 `plan_version=KW-P0-2026-11-18` 不变。
- 旧条文记载监控器每90分钟只读检查可见快照（该周期已被后续用户决定取代且未验证）；其余规则继续有效：在 `state/monitoring/reports/` 以唯一 `report_id` 创建报告JSON，原子更新 `state/monitoring/latest.json`，复读JSON并校验SHA-256。历史报告不可改写。监控器只检查、记录和持久化，禁止尝试发送消息或提醒，也不得派工、批准、验收、集成或修改账本/checkpoint。
- 唯一Orchestrator每次启动/恢复读取latest并扫描所有未消费报告，核对report_id、路径、SHA-256和JSON，将报告与真实会话/Git/账本对账；追加含 `report_id`、`path`、`sha256`、`consumed_at`、`ledger_revision`、`outcome` 的消费记录及runtime event。相同report_id不得重复处理；冲突或校验失败时不改写报告并记录阻塞。
- P2门槛为真实定时heartbeat触发、报告持久化与SHA复核、Orchestrator启动/恢复读取及report_id幂等消费。向Orchestrator直接发送/返回报告不再是门槛；此项是用户决定对旧传递方式要求的明确替换，先前独立verifier判定按当时标准保留，不改写其历史。
- 当时的90分钟周期文字来自较早请求；实际现行automation为ACTIVE、每15分钟，使用独立monitor thread `01a1efc6-349f-7b92-83a0-c1c242524176`，其提示词禁止direct send。该实际周期由后续用户决定保持，本增补不重新配置它。
- 不改技术架构、算法、接口、Batch依赖或已完成的P1/Docker/Python成果；不启动B00–B08。本决议只增补执行/恢复协议。

### KW-USER-DECISION-P2-MONITOR-CADENCE-20261119 — 2026-11-19

- 用户明确决定保持现有监控周期每15分钟，不改automation配置；当前automation实读为 `ACTIVE`、`RRULE:FREQ=MINUTELY;INTERVAL=15`，monitor thread为 `01a1efc6-349f-7b92-83a0-c1c242524176`。
- 此决定取代此前要求/文档中出现的90分钟周期表述。P2交付中两份约15分钟间隔的真实报告触发及持久化证据继续有效；不得声称90分钟周期已启用或验证。
- 监控器仍只读生成并持久化报告；Orchestrator每次恢复读取latest及所有未消费报告，按 `report_id` 幂等消费。监控器不得直接发送消息或提醒，传递规则不变。
- `plan_version=KW-P0-2026-11-18` 与原P0批准SHA-256 `6ef838b43dc6a6a8d1680c8b9503e55fe7a63c6ca9c4238e4942acf8d495d1df` 不变。本记录为授权的文档规则增补，不修改manifest或冻结基线。
