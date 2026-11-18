# KnowledgeWeaver

Multi-source Knowledge Intelligence and Agentic Retrieval Platform — 多源异构知识理解与自主检索 Agent。

这是从零建立的初始正式规划，plan_version=KW-P0-2026-11-18。当前仅交付文档，无业务代码、无已完成 Batch、无已验证基础设施。本目录不是任何旧项目的恢复或升级。

入口：[规划路线](docs/planning/RESTART_PLAN.md) → [产品需求](docs/planning/REQUIREMENTS.md) → [算法](docs/planning/ALGORITHM_DESIGN.md) → [架构](docs/planning/ARCHITECTURE.md) → [数据模型](docs/planning/DATA_MODEL.md) → [接口](docs/planning/INTERFACES.md)。后续执行读取 [Orchestrator 协议](docs/planning/ORCHESTRATOR_PROTOCOL.md) 和 [完整启动提示词](templates/ORCHESTRATOR_START.md)。

Phase 0 资料检查通过后只标 READY_FOR_REVIEW；架构审查通过、记录批准后才可启动 Phase 1。Phase 1 的独立任务入口：[P1 环境准备](docs/phases/P1_ENVIRONMENT.md)。不得立即执行 B00。

运行约束：Windows 11 原生 Python 3.11/uv/Git/PowerShell；Docker Desktop 只承载 PostgreSQL/Qdrant；每 Batch 独立 Work 会话和 Worktree；Orchestrator 本地自动集成，无日常 GitHub PR 门槛。最多 3 个开发会话。

完整文档与文件 hash 见 PLAN_MANIFEST.json；核查事实见 docs/planning/CAPABILITY_AUDIT.md；待确认事项见 docs/planning/RISKS_AND_DECISIONS.md。Phase 0 完成情况与项目整体完成情况必须分开。
