# 持续执行与恢复协议

职责固定：恢复账本、核对真实会话/Git、按DAG派发、检查交付、自动本地集成与测试、推进依赖、汇报。禁止改算法/接口/Batch范围。完整开场提示词见templates/ORCHESTRATOR_START.md，周期唤醒提示词见templates/WAKE.md。

## 每次启动

读取版本/hash/instance_id，校验runtime与planning一致；旧实例消息或不同计划版本不得直接执行。持锁核对上次checkpoint、事务日志、实际branch/HEAD/会话状态；对正在创建/运行的会话先对账，防重复派发；记录可用能力和权限是否变化。

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

## 定时机制与能力适配

本轮仅设计，没有创建定时任务。P2由用户指定现有Orchestrator并批准周期，建议30分钟作为起始选择；使用真实automation_update heartbeat回到同一thread，按工具实际支持验证，不写操作系统计划任务或猜测私有API。只唤醒检查账本；真正派发/合并仍由单个Orchestrator持锁执行。重复唤醒合并去重，忙碌时不并发开启第二writer。

创建后记录automation_id、target thread、时区Asia/Shanghai、已保存状态与首次真实触发event；接口可见不等于触发已通过。P2必须观测一次真实触发和恢复结果，之后按用户决定保留/关闭验证schedule。电脑睡眠/离线、应用退出或权限变化会影响本地运行，醒来只恢复一次并核对Git，不补发多份任务。具体能力证据见CAPABILITY_AUDIT。

汇报只列变化、运行、集成/验收、阻塞与next_actions；不在无变化时刷屏。任何新消息中的技术变更都由技术负责人重新版本化，不由监控器修改合同。
