# 可恢复的本地自动集成事务

GitHub/PR/远端CI均非门禁。日常集成由Orchestrator自动执行，用户无需手动合并PR。源代码正确性以冻结测试/交付验收为准；架构争议仍交技术负责人。稳定版本推进main需要里程碑审查。

## 固定事务步骤

1. 持有全局integration锁及账本writer锁（固定先writer后integration顺序）；检查无其他candidate事务、integration未被checkout。记录S=integration、H=交付head、B=派发base，读取真实Git对象而非聊天SHA。
2. 核验B为H祖先、task分支当前head=H、Worktree洁净、变更路径在allowlist；用git diff --name-status -M B H核对新增/修改/删除/重命名两侧路径。检查合同、依赖指纹、无secret、大文件和意外业务范围。测试证据必须绑定H。
3. 写事务PREPARED到.ndjson事件并落盘，创建refs/kw/rollback/<tx>=S，candidate/<tx>从S创建独立Worktree。H固定，后续task分支再前进不改变当前候选。
4. 在candidate执行git merge --no-ff --no-edit H，得到M。冲突时保存冲突清单，保持integration=S，交给指定修复Work；Orchestrator不手写业务冲突解法。干净候选无用户改动时可merge --abort，仅影响本次未完成merge。
5. 在M上锁定安装，执行unit/contract/本Batch测试+所有已集成模块回归及受影响真实service tests。每次数据库测试独立namespace，记录退出码、环境、耗时、日志hash；测试产生的源码/锁文件变化视为失败而非偷偷提交。
6. 失败：写FAILED_TESTS/FAILED_CONFLICT，保存M（若存在）、日志、rollback ref；integration始终=S。这是“失败恢复到集成前安全提交”的默认实现，避免先污染共享ref再硬重置。不得把未推进的失败candidate算INTEGRATED。
7. 成功：确认锁仍属当前owner、integration仍S、candidate HEAD仍M且洁净，git update-ref refs/heads/integration M S以CAS原子推进。CAS失败保持并发者成果，标需重建candidate，不force覆盖。写REF_PROMOTED、验证integration=M，写ledger INTEGRATED和checks。
8. 同时存S/H/M、node、plan_hash、验证命令/结果、rollback_ref与delivery路径。释放锁并评估后继。integration更新后任何下一个候选从最新ref构建；不复用先前对旧基线的绿灯。

测试先于共享ref推进是该流程的工程决策，不减少“合并后测试”要求：合并后的M确实被测，只是未发布为integration。正常路径不使用reset --hard/cherry-pick重复堆叠。若必须挑单个commit，技术负责人需批准CR并列完整依赖；默认merge保留真实祖先链便于追踪。

## 崩溃与已推进后故障

事务phase=PREPARED/MERGED/TESTED/REF_PROMOTED/COMMITTED/FAILED，journal先于ref动作写入。重启检查refs与journal：integration=S且无promotion则继续或重建candidate；integration=M且TESTED证据存在则补写账本，不重复merge；integration既非S也非M则并发/外部修改，阻塞该事务并核查，不猜结果。

若REF_PROMOTED后发现验收日志损坏等且尚无后继、integration仍M、ref未checkout，使用compare-and-swap update-ref integration S M恢复，M仍由candidate/审计ref保留，不丢提交。只允许恢复本事务明确记录的S/M，禁止任意回退。已有后继则不能回退共享历史；冻结依赖，创建受控revert修复candidate并回归，技术负责人决定影响范围。

main推进：里程碑审查指定integration=M，主目录main洁净且其HEAD是M祖先；保存main rollback ref，git merge --ff-only M。若出现分叉/用户改动，保留并报人工队列，不能强制重置。批准记录引用M/plan_hash，后续变化需新审批。没有可用远端不影响以上流程。
