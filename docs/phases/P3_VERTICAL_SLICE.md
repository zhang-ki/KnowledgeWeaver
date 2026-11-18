# P3 — 最小技术纵切片验证

## 目标与依赖

P2 COMPLETED；真实provider的数据外发与预算授权、PG/Qdrant可用。

## 固定任务

只在隔离原型目录建立PDF+Python最小manifest/结构chunk/静态和文本关系/持久Evidence；真实PG/Qdrant与embedding索引；BM25+Dense初检及一条可核验跨源两跳；用公开prior配置做状态/缺口/预算/停止闭环；真实LLM输出有引用的claim；同时演示无证据/冲突/超时；记录parser/model/成本和合同缺口，需改合同先CR；完整开发仍由后续B00–B08承担。

## 允许范围

experiments/vertical_slice/、tests/vertical_slice/、docs/implementation/P3.md、隔离test corpus/collection；不建立或导入正式src/knowledge_weaver业务模块。

## 验收与交付

真实端到端、证据页/行可核验、两个不同问题路由有区别、预算不超、缺证据明确不足；Fake只能支撑单测，不能替代此真实gate。 交付docs/deliveries/P3.md、真实命令/退出码/时间/环境、artifact hash、Git/session/事务证据。用templates/DELIVERY.md。不得只报“成功”。

## 完整独立Work启动提示词

```text
你负责KnowledgeWeaver P3：最小技术纵切片验证，计划KW-P0-2026-11-18。项目D:\agent\KnowledgeWeaver；本轮执行阶段必须由用户明确启动。
先读AGENTS.md、docs/planning/RESTART_PLAN.md、对应环境/协作/数据/接口文档、ACCEPTANCE、TASK_LEDGER和本文件。核对instance/plan_hash/阶段批准、真实cwd/Git状态，保留已有文件。
前置：P2 COMPLETED；真实provider的数据外发与预算授权、PG/Qdrant可用。
任务：只在隔离原型目录建立PDF+Python最小manifest/结构chunk/静态和文本关系/持久Evidence；真实PG/Qdrant与embedding索引；BM25+Dense初检及一条可核验跨源两跳；用公开prior配置做状态/缺口/预算/停止闭环；真实LLM输出有引用的claim；同时演示无证据/冲突/超时；记录parser/model/成本和合同缺口，需改合同先CR；完整开发仍由后续B00–B08承担。
允许范围：experiments/vertical_slice/、tests/vertical_slice/、docs/implementation/P3.md、隔离test corpus/collection；不建立或导入正式src/knowledge_weaver业务模块。
验收：真实端到端、证据页/行可核验、两个不同问题路由有区别、预算不超、缺证据明确不足；Fake只能支撑单测，不能替代此真实gate。
不得提前启动正式Batch、创建远端或调用未授权付费API；需要安装/系统变更/账户授权时先给具体方案再等待授权，继续不依赖该决定的项目内工作。不自行改冻结合同，冲突提CR。
交付真实日志、hash、commit/session记录与docs/deliveries/P3.md，报告未完成门禁；完成Phase任务不等于整个项目完成。Orchestrator按固定门禁核实并恢复/推进后续阶段。
```
