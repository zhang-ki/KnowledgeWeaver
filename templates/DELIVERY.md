# Work交付模板

- task_id/batch_id/attempt_id/instance_id/plan_version/plan_hash：
- 实际worktree/branch/base_commit/head_commit：
- 目标与实现清单；逐文件allowlist映射：
- 公共接口/配置指纹与依赖交付：
- 测试命令、退出码、时间、环境、pass/fail/skip、日志路径/hash：
- 真实服务/provider与Fake的明确区分：
- 验收逐项证据、known limitations、blocked事项：
- artifact路径/hash和后继消费步骤：
- 申请READY_FOR_REVIEW，不自行写INTEGRATED/COMPLETED。

报告提交内head_commit可空；会话最终返回实际Git HEAD，Orchestrator将它与报告blob hash关联。避免为了写自身SHA不断补提交。交付JSON字段按DELIVERY.schema.json；无PR字段要求。
