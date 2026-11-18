# 持续执行与恢复协议

职责固定：恢复账本、核对真实会话/Git、按DAG派发、检查交付、自动本地集成与测试、推进依赖、汇报。禁止改算法/接口/Batch范围。完整开场提示词见templates/ORCHESTRATOR_START.md，周期唤醒提示词见templates/WAKE.md。

## 每次启动

读取版本/hash/instance_id，校验runtime与planning一致；旧实例消息或不同计划版本不得直接执行。持锁核对上次checkpoint、事务日志、实际branch/HEAD/会话状态；对正在创建/运行的会话先对账，防重复派发；记录可用能力和权限是否变化。每次新启动或恢复均须读取 `state/monitoring/latest.json` 并枚举尚未消费的监控报告，先校验、复读和对账，再继续验收、集成或派发；消费字段与幂等规则见下文及TASK_LEDGER。

## 运行循环

1. 收取活动会话状态与提交交付，Git优先；已结束会话无commit/交付为FAILED，不直接完成。
2. 恢复未完成integration事务；对READY_FOR_REVIEW按提交时间再node排序串行集成。
3. 处理可自动解除的BLOCKED：已获授权的服务重启、暂时网络恢复、运行失败重试等。无安装/付费授权时不自动安装/付费。凭据缺失无意义重试次数为0。
4. 根据阶段/依赖/所需能力计算READY。在3槽上限内按B00、B01、B02、B03、B04、B05、B06、B07.prep、B07.final、B08固定优先级派发；P1/P2/P3只按阶段顺序。B00后初次集合自然是B01/B02/B07.prep。
5. 写事件、checkpoint和next_actions。继续循环，直到无本轮可执行动作。

## 结束前七项检查

READY；IN_PROGRESS；可自动解除BLOCKED；待集成commit；需修复测试失败；可启动后继；需用户决定事项。任何一项有可执行动作就继续。仍有活动会话用wait_threads有界等待，保留恢复cursor，不结束成“项目完成”。没有立即动作但有运行会话为WAITING_RUNNING；全部剩余为外部条件则WAITING_EXTERNAL并给明确决定清单。用户暂停才PAUSED。

只在所有必需节点COMPLETED、无活动会话/待集成/失败/人工队列且最终release审查通过才PROJECT_COMPLETED。本轮Phase0交付最多READY_FOR_REVIEW。工具/进程有限生命周期：不能保证单次聊天永不结束，靠落盘checkpoint与真实可用的周期唤醒恢复；没有有效唤醒机制就明确需要用户重新发消息，不能声称后台持续工作。

## 重试与人工队列

瞬态工具调用重试≤2，失败实现修复attempt≤2（总最多3轮）；都记录原因与结果。有限重试后BLOCKED，不无限轮询。架构争议、系统/账户权限、收费授权、不可逆数据操作立即人工队列。单项阻塞只影响其依赖闭包；无remote永不成为本地开发阻塞；无云Key只阻塞live gate；PG离线不阻塞算法单测，但阻塞对应service验收。

## 定时快照与 Orchestrator 报告消费

协议增补：`KW-ORCH-REPORT-POLL-2026-11-19-v1`，依据决议 `KW-DEC-ORCH-REPORT-POLL-20261119`。`plan_version` 仍为 `KW-P0-2026-11-18`。本增补只规定监控报告的传递和消费，不改变技术架构、算法、接口、Batch依赖或已完成的P1/Docker/Python成果。

监控器按用户于2026-11-19确认的每15分钟周期对可读的运行快照做只读检查；当前 automation 为 ACTIVE，使用独立监控 thread `01a1efc6-349f-7b92-83a0-c1c242524176`，配置保持不变。此前90分钟请求已被后续决定 `KW-USER-DECISION-P2-MONITOR-CADENCE-20261119` 取代；没有声称90分钟周期曾启用或通过验证。P2交付保留了15分钟真实触发与持久化报告证据。每轮生成唯一 `report_id`，只在 `state/monitoring/reports/` 写入一份报告JSON，并原子更新 `state/monitoring/latest.json` 指向报告路径及SHA-256。写完后复读JSON、重算报告SHA-256并核对指针。报告创建后不可改写；更正只能以新 `report_id` 新建报告。监控器只检查、记录、持久化，不得发送消息、提醒或尝试直接投递，不得派工、批准、验收、集成、改账本/checkpoint或触碰其他运行状态。读取受限时如实记为UNKNOWN和错误码。

Orchestrator在每次启动或恢复、取得唯一writer锁后，先读取 `latest.json`，并枚举报告目录中所有没有有效消费记录的报告；`latest.json` 只是最新指针，不能代替扫描未消费报告。逐份核对 `report_id`、路径、报告SHA-256和JSON，再将报告声明与当前账本、真实会话及Git引用对账；过期快照仍须被处理，但其陈旧状态不得作为当前事实。校验失败时不得使用报告内容或改写报告，应记录完整性阻塞及runtime event并等待处理。

每份报告消费后，由Orchestrator在runtime消费记录中追加 `report_id`、`path`、`sha256`、`consumed_at`、`ledger_revision`、`outcome`，并追加带相同 `report_id` 与SHA-256的runtime event。`outcome` 至少区分 `RECONCILED`、`STALE_RECONCILED`、`NO_OP` 和 `BLOCKED_INTEGRITY`。消费状态记录在 `.kw/runtime/monitoring-consumption.json`；监控器无权写入。消费以 `report_id` 幂等：同一 `report_id`、路径和SHA已记录时跳过，不再次协调或追加重复事件；同ID出现不同路径或SHA时作为完整性冲突阻塞，不覆盖历史。所有报告历史不可变。报告消费和核验完成后，Orchestrator才继续本轮交付验收、integration恢复/集成、派发与drain检查。直接报告投递不再是P2门槛，具体以决议记录为准。

P2仍须有真实周期heartbeat触发、报告持久化并校验、Orchestrator启动/恢复读取和按 `report_id` 去重消费的证据；仅配置可见或人工生成文件不算真实触发。现有触发与报告证据支持15分钟周期，不构成90分钟周期验证。电脑睡眠/离线、应用退出或权限变化会影响本地运行；恢复时只消费已持久化报告并核验Git，不补发多份任务。具体工具能力证据见CAPABILITY_AUDIT。

汇报只列变化、运行、集成/验收、阻塞与next_actions；不在无变化时刷屏。任何新消息中的技术变更都由技术负责人重新版本化，不由监控器修改合同。
