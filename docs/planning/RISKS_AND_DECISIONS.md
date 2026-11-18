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
| 调度提前结束 | 持久账本+drain七项检查+已验证周期唤醒；等待状态不称完成 |
| 历史任务误通知 | 全新instance_id、显式Orchestrator绑定、旧版本消息拒绝；不恢复旧代码 |
| 人工审查瓶颈 | 在B07.prep提前准备；缺Gold审校只阻塞对应gate，不假签审查 |

需要用户在对应阶段确认：

1. P0：当前plan_hash架构审查批准与审查人身份；当前状态仅READY_FOR_REVIEW。
2. P1：安装/配置Python3.11、uv、Docker Desktop的授权；Windows/虚拟化/磁盘可用性、Docker许可适用性；密码由本地生成/配置。
3. P1/P3：云LLM/embedding/reranker候选endpoint与model、允许外发哪些资料、单次与实验总预算、价格快照；本轮不收集Key。
4. P2：实际Orchestrator会话和本地project绑定、创建probe会话/提交/集成的执行授权；周期唤醒频率/时区与可保持电脑在线的条件，建议30分钟待定。
5. B07：合法语料与人工Gold/答案评审人员，正式矩阵估算费用授权；不足时按CR调整范围。
6. RELEASE：可选GitHub账户/可见性/发布授权，缺失不阻塞本地开发与本地演示。

当前阻塞是后续动作尚未授权/验证，不阻塞Phase0文档交付。安装、系统级配置、远端创建、收费调用、不可逆删除不随文档生成自动发生。
