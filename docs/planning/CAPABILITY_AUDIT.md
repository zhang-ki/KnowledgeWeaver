# 当前环境能力核查（2026-11-18）

本轮允许只读核查，创建/提交/集成/定时触发的真实演练明确安排在P2；不能为验证而提前执行P2。下表把接口声明、只读成功和端到端验证分开。

| 能力 | 当前证据 | 本轮状态 | P2 必须补证据/替代 |
|---|---|---|---|
| 项目定位 | list_projects 返回KnowledgeWeaver路径D:\agent\KnowledgeWeaver，isGitRepository=false | 只读已验证 | P1新建repo后再次核实 |
| 独立会话 | create_thread支持project/local或worktree；需要真实projectId与Git repo | 工具声明可用，未创建 | 实际返回threadId/hostId，任务独立cwd |
| Worktree | create_worktree可从sourceRoot/ref创建，返回path或operationId；默认detached | 工具声明可用，未创建 | 检查真实path/HEAD/独立branch；优先create_thread(worktree)一次完成 |
| 状态查询 | list_threads本轮只读调用成功；wait_threads可timeoutMs=0或有界等待 | 清单读取已验证；目标等待未测 | 对probe任务核对active→idle与cursor |
| 消息投递 | send_message_to_thread接口存在，要求人类授权目标消息 | 未发送 | 用户授权后发送nonce，接收任务回显并落盘 |
| 子会话结果 | read_thread/wait_threads接口存在 | 当前probe结果未验证 | 对比返回结果与提交文件，不仅看final文字 |
| 周期触发 | automation_update存在heartbeat类型可针对既有thread | 未创建/未触发 | 真实保存后观测一次触发、账本恢复；无能力则P2 gate未通过 |

实际已知工具名称均来自当前可用工具schema，不是自建REST API。create_thread是非阻塞；clientThreadId不能冒充threadId。仅创建worktree不等于已创建会话。新会话模型默认采用用户配置，不擅自固定模型。能力可能随产品权限/版本改变，P2与恢复时都要复核。

本轮list_threads返回了若干同名相关聊天，但不读取其历史、不恢复其工作、不把它们视为本项目当前Orchestrator。新实例的orchestrator_session_id初始null；P2绑定用户指定会话并校验instance_id，防止历史监控误写新项目。既有自动化是否活跃本轮未核查/未修改，P2开始前只读盘点并由用户决定处理。

不能自动绑定worktree/唤醒时的可行受限方案是用户手动开启独立会话/唤醒，Git仍可本地自动集成；但这不满足原定全自动P2验收，不得自行宣布通过，需记录限制与技术负责人批准的合同修订。

官方说明支持桌面本地任务和定时任务的总体机制，但不替代本机实测：[Work入口](https://learn.chatgpt.com/docs/get-started-with-work)、[定时任务](https://learn.chatgpt.com/docs/automations)。本项目使用Codex桌面本地独立会话，不把云任务或Agents API的能力等同于已验证的本地权限。
