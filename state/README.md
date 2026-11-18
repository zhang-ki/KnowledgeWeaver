# 状态与证据目录

planning/记录本次从零规划、依赖表、能力核查与文档验收；没有业务运行进度。后续Orchestrator按TASK_LEDGER create-only初始化主项目.kw/runtime；不覆盖已有运行记录。所有Worktree读取主runtime绝对路径，只有单个Orchestrator持锁写。

tasks.json中P0文档检查通过才READY_FOR_REVIEW，其余PENDING；null会话/commit/provider不是已完成。project_instance_id为本次新实例，拒绝不同实例消息。架构批准放planning/reviews/，使用templates/REVIEW.md；本轮没有批准记录。
