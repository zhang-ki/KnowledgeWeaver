# P2 — 独立Work协作与恢复验证

## 目标与依赖

P1 COMPLETED；用户已批准本阶段创建probe会话/本地提交与集成、指定Orchestrator及唤醒周期。当前automation为ACTIVE、每15分钟，用户决定保持现有周期且不改配置（`KW-USER-DECISION-P2-MONITOR-CADENCE-20261119`）。

## 固定任务

读取实际工具schema并记录能力；在新repo创建probe/base和probe/integration refs，不改正式integration；创建A/B两个独立Worktree会话，各改verification/probes/a.txt或b.txt并独立提交，核对cwd/branch；发送授权nonce消息并取得回显；按AUTO_INTEGRATION把提交候选合并到probe/integration并测试；创建C故意失败候选，证明ref仍在安全S；另做同一行冲突且不覆盖；在TESTED与REF_PROMOTED之间模拟中断，用journal恢复一次且不重复合并；验证writer锁与重复派发去重。监控闭环使用用户指定的独立monitor thread和现有heartbeat：真实周期触发后仅生成唯一持久报告、原子更新latest并复读校验SHA；Orchestrator每次启动/恢复读取latest及全部未消费报告，逐份核SHA并与真实会话/Git对账，按report_id幂等记录消费字段和runtime event。当前automation为ACTIVE、每15分钟，监控thread `01a1efc6-349f-7b92-83a0-c1c242524176`；保持配置不变。

## 允许范围

verification/probes/、scripts/orchestration/、tests/orchestration/、.kw/runtime/probe/、probe/*与candidate/probe-*refs；正式业务目录禁止。

## 验收与交付

每项都有真实session/path/SHA/log/事件；失败候选不污染ref，冲突无强制覆盖。监控门槛是观测到真实定时heartbeat、报告已持久化且SHA复核通过、Orchestrator启动/恢复后读取latest及所有未消费报告并以report_id去重消费；当前周期按用户决议保持ACTIVE、每15分钟，且不改配置。直接向Orchestrator发送/返回报告已由用户决议 `KW-DEC-ORCH-REPORT-POLL-20261119` 替换，不再是门槛，也不得由监控器尝试发送消息或提醒。只检查接口存在不算通过。任一关键能力缺失阻塞P2并给受限替代，不能开启P3/B00。交付docs/deliveries/P2.md、真实命令/退出码/时间/环境、artifact hash、Git/session/事务证据。用templates/DELIVERY.md。不得只报“成功”。

## 完整独立Work启动提示词

```text
你负责KnowledgeWeaver P2：独立Work协作与恢复验证，计划KW-P0-2026-11-18。项目D:\agent\KnowledgeWeaver；本轮执行阶段必须由用户明确启动。
先读AGENTS.md、docs/planning/RESTART_PLAN.md、对应环境/协作/数据/接口文档、ACCEPTANCE、TASK_LEDGER和本文件。核对instance/plan_hash/阶段批准、真实cwd/Git状态，保留已有文件。
前置：P1 COMPLETED；用户已批准本阶段创建probe会话/本地提交与集成、指定Orchestrator及唤醒周期。
任务：读取实际工具schema并记录能力；在新repo创建probe/base和probe/integration refs，不改正式integration；创建A/B两个独立Worktree会话，各改verification/probes/a.txt或b.txt并独立提交，核对cwd/branch；发送授权nonce消息并取得回显；按AUTO_INTEGRATION把提交候选合并到probe/integration并测试；创建C故意失败候选，证明ref仍在安全S；另做同一行冲突且不覆盖；在TESTED与REF_PROMOTED之间模拟中断，用journal恢复一次且不重复合并；验证writer锁与重复派发去重。不要创建或重配监控任务；核验真实周期heartbeat触发及其持久化报告、latest回读和SHA证据；当前automation设置为ACTIVE、每15分钟，保持配置不变。监控器只持久化快照报告，Orchestrator每次启动/恢复读取latest和所有未消费报告，逐份以report_id去重、核SHA、与真实会话/Git协调，记录消费状态与runtime event后再继续验收/集成/派发。直接报告返回不再要求（决议 `KW-DEC-ORCH-REPORT-POLL-20261119`）。
允许范围：verification/probes/、scripts/orchestration/、tests/orchestration/、.kw/runtime/probe/、probe/*与candidate/probe-*refs；正式业务目录禁止。
验收：每项都有真实session/path/SHA/log/事件；失败候选不污染ref，冲突无强制覆盖，实际定时触发被观察。只检查接口存在不算通过。任一关键能力缺失阻塞P2并给受限替代，不能开启P3/B00。
不得提前启动正式Batch、创建远端或调用未授权付费API；需要安装/系统变更/账户授权时先给具体方案再等待授权，继续不依赖该决定的项目内工作。不自行改冻结合同，冲突提CR。
交付真实日志、hash、commit/session记录与docs/deliveries/P2.md，报告未完成门禁；完成Phase任务不等于整个项目完成。Orchestrator按固定门禁核实并恢复/推进后续阶段。
```
