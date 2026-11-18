# 持久账本、状态与幂等规则

state/planning/tasks.json是本次规划快照；执行时由Orchestrator首次create-only复制到主项目.kw/runtime/tasks.json。所有Worktree引用同一绝对runtime路径，不能每棵树各维护一份真相。runtime gitignored，规划模板可入Git。补写状态不能修改原始技术计划。

顶层：schema_version、plan_version、project_instance_id、orchestrator_session_id、revision、project_status、tasks、human_queue、integration_transaction。每任务必含task_id,batch_id,phase,status,dependencies,assigned_session_id,host_id,worktree,branch,base_commit,head_commit,retry_count,last_checkpoint,created_at,updated_at,blocked_reason,delivery_status；另有attempt_id,active_session,integrated_commit,gate_evidence,required_capabilities,artifact_hashes。

状态：PENDING→READY→IN_PROGRESS→READY_FOR_REVIEW→INTEGRATING→INTEGRATED→COMPLETED。Phase0无code，READY_FOR_REVIEW经人工审查批准可直接COMPLETED；P1/P2/P3以固定gate完成，代码证据如有也需本地集成。Batch INTEGRATED表示交付commit已在integration且测试通过，可解锁下游；COMPLETED表示所在里程碑正式验收/稳定main包含成果，两者不能混淆。B07.prep INTEGRATED只标prepared，不代表B07整体完成。

失败路径：任一执行态可FAILED或BLOCKED；FAILED在retry_count<2且问题可自动修复时READY（新attempt），否则BLOCKED进入human_queue。BLOCKED解除后重新判断依赖与现有会话，恢复IN_PROGRESS或READY，不能无条件另开会话。已集成成果被撤销/缺证据时标BLOCKED并写reason=INTEGRATION_INVALIDATED，受影响下游同样暂停，事件保留原状态。

delivery_status=NOT_STARTED/DRAFT/SUBMITTED/VALIDATED/REJECTED；SUBMITTED不等于通过。last_checkpoint={kind,artifact_path,sha256,observed_commit,event_seq}，不得仅写“上次完成一半”。依赖记录含task_id和required_status（阶段COMPLETED、业务INTEGRATED或COMPLETED），所有标准从DAG自动计算，不手写猜测。

写入：仅Orchestrator使用OS级排他文件句柄锁（Windows可用受控msvcrt锁实现），读当前revision，写临时JSON→flush/fsync→同目录os.replace，追加event with monotonic seq/event_id/checksum；读者遇到暂时sharing violation有限重试。锁owner/heartbeat只是诊断，不能仅靠过期时间抢活锁。新进程确认旧writer退出才恢复；无法确定时先读事实并等待。

派发意图先记录dispatch_id/node/attempt/base/branch再创建会话；返回threadId或待完成clientThreadId分别存，不把后者传给read/wait。调用超时先按dispatch_id及会话清单对账，不盲重试创建。集成事务写ahead journal；技术细节见AUTO_INTEGRATION。

event字段：id,seq,at,actor,node,old,new,reason,commit,artifact；人工队列字段：id,node,reason,decision_required,owner,created_at,resolution。禁止Key、完整私有原文和未脱敏连接串。每完成事务备份账本快照到.kw/runtime/snapshots，留最近20份加里程碑永久快照；备份不包含secrets。

## 监控报告消费账本

监控器只写 `state/monitoring/reports/<report_id>.json` 和 `state/monitoring/latest.json`；它不得写 `.kw/runtime`、主任务账本、checkpoint 或 events。报告写入后不可改写。唯一 Orchestrator 在每次启动/恢复时读取 `latest.json`，同时扫描所有未消费报告；验证报告的 `report_id`、路径、JSON及SHA-256，并以当前会话/Git/账本事实核对快照后，才继续派发、验收或集成。

Orchestrator把消费状态追加到 `.kw/runtime/monitoring-consumption.json`，每份新记录必含：`report_id`、`path`、`sha256`、`consumed_at`、`ledger_revision`、`outcome`；并追加带有相同report_id/SHA的runtime event。`outcome` 应区分已对账、过期后已对账、无动作和完整性阻塞。记录以report_id唯一；已有相同report_id/path/SHA记录时不得重复处理或重复记event。相同report_id出现不同path或SHA时按完整性冲突阻塞，不覆盖任何历史记录。过期报告仍需对账，不能直接采信为当前事实。SHA或JSON校验失败时不读取其结论、不修改原报告，并记录阻塞与runtime event。

报告记录、event和账本更新都由Orchestrator单写锁保护。消费记录的 `ledger_revision` 表示完成该报告对账时已持久化的账本revision；随后记录的event引用同一report_id与SHA。重启遇到已有消费记录时按上述幂等规则继续，不重做已完成的报告协调。旧消费记录保持原样；本字段要求适用于本增补生效后的新记录。
