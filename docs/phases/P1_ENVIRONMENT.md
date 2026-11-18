# P1 — Windows原生环境准备

## 目标与依赖

P0审查批准已记录；安装/系统级动作先逐项获用户授权。

## 固定任务

盘点Python/uv/Git/PowerShell/DockerDesktop；配置独立3.11与uv工具环境；验证Docling普通PDF小样例的Windows兼容与资源开销；按固定tag验证镜像/安全公告并锁digest；创建双服务Compose与Windows健康检查；配置loopback、命名卷、环境变量和秘密脱敏；完成隔离持久化/备份恢复；建立验证工具pyproject/锁与verification测试，不建立业务src；授权后创建新Git main基线/integration并核对Codex project Git识别。

## 允许范围

infra/、verification/environment/、pyproject.toml、uv.lock、.python-version、environment.lock.json、docs/implementation/P1.md和交付；规划变化须CR。

## 验收与交付

真实3.11解释器、命令路径/版本、PG SELECT1/Qdrant隔离写读、重启保留、恢复hash一致、Windows测试与配置缺失/脱敏检查。模型Key可等待，不阻塞其他项；P3 live gate保持等待。 交付docs/deliveries/P1.md、真实命令/退出码/时间/环境、artifact hash、Git/session/事务证据。用templates/DELIVERY.md。不得只报“成功”。

## 完整独立Work启动提示词

```text
你负责KnowledgeWeaver P1：Windows原生环境准备，计划KW-P0-2026-11-18。项目D:\agent\KnowledgeWeaver；本轮执行阶段必须由用户明确启动。
先读AGENTS.md、docs/planning/RESTART_PLAN.md、对应环境/协作/数据/接口文档、ACCEPTANCE、TASK_LEDGER和本文件。核对instance/plan_hash/阶段批准、真实cwd/Git状态，保留已有文件。
前置：P0审查批准已记录；安装/系统级动作先逐项获用户授权。
任务：盘点Python/uv/Git/PowerShell/DockerDesktop；配置独立3.11与uv工具环境；验证Docling普通PDF小样例的Windows兼容与资源开销；按固定tag验证镜像/安全公告并锁digest；创建双服务Compose与Windows健康检查；配置loopback、命名卷、环境变量和秘密脱敏；完成隔离持久化/备份恢复；建立验证工具pyproject/锁与verification测试，不建立业务src；授权后创建新Git main基线/integration并核对Codex project Git识别。
允许范围：infra/、verification/environment/、pyproject.toml、uv.lock、.python-version、environment.lock.json、docs/implementation/P1.md和交付；规划变化须CR。
验收：真实3.11解释器、命令路径/版本、PG SELECT1/Qdrant隔离写读、重启保留、恢复hash一致、Windows测试与配置缺失/脱敏检查。模型Key可等待，不阻塞其他项；P3 live gate保持等待。
不得提前启动正式Batch、创建远端或调用未授权付费API；需要安装/系统变更/账户授权时先给具体方案再等待授权，继续不依赖该决定的项目内工作。不自行改冻结合同，冲突提CR。
交付真实日志、hash、commit/session记录与docs/deliveries/P1.md，报告未完成门禁；完成Phase任务不等于整个项目完成。Orchestrator按固定门禁核实并恢复/推进后续阶段。
```
