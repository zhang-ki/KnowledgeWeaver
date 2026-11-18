# 独立会话、Worktree 与分支所有权

最大3个活动开发会话（包括修复/派发中的会话），Orchestrator和只读唤醒不占槽。一个node/attempt只有一个当前session+worktree+branch。停止会话并确认后才释放槽，不能因聊天无更新就认为结束。

main保存稳定验收版本，主目录允许checkout main；integration是开发集成ref，刻意不在任何worktree中checkout，便于候选测试后CAS推进。task/b00-foundation等是独立开发分支；candidate/<transaction_id>是临时集成分支；refs/kw/rollback/<transaction_id>永久保留回滚点直到明确清理授权。probe/*仅Phase2工程验证。

P1授权后git init -b main，提交审查通过的规划和环境基线，建立integration。每派发前固定base_commit=当时integration SHA并核实依赖成果已可追溯。正常用Codex create_thread的project/worktree模式，从已存在integration ref开始，保存返回的真实workspace目录；子会话首个动作检查HEAD=base并切换/建立唯一task分支。主机工具若返回已有分支，则使用真实命名并登记，不能假造路径或让两个会话共享目录。

不要同时为一次派发调用create_thread(worktree)与create_worktree，避免重复checkout。若前者不可用，仅create_worktree成功并不能证明会话绑定成功；必须找到可验证的绑定方式再派发。Git CLI可创建checkout，但不能代替独立会话。替代为用户手动建立独立会话并确认cwd，属于受限方式，需技术负责人调整P2验收后才能进入正式并行。

每个Work只读取本计划、对应Batch、合同和依赖交付物，禁止复制所有其他会话历史。不直接改main/integration、不自行合并、不创建更多开发会话。已有用户修改导致不洁净时保留内容并报冲突；不能自动stash未知改动。按allowlist提交准确文件，禁止git add .意外带入秘密/私有语料。根.gitattributes将规划与源码固定LF换行；plan_hash以清单指定文件的LF字节计算，避免Windows checkout换行转换造成假版本冲突。

共享文件所有者：P1掌管环境基线；B00掌管models/ports/core/providers/依赖锁/共用fixtures；B01–B07只拥有各自模块和测试。B08只增加API/UI/CLI及预先指定的entrypoint行。新增依赖/公共schema变更先CR，技术负责人指定唯一Work实施，其后消费者同步基线重新验证。不能靠最后提交覆盖。

分支滞后允许普通merge进候选并回归，不能仅以base旧就丢弃成果；base必须是head祖先且原始派发记录一致。互相挑拣未集成Batch提交、force push、rebase改写已交付历史禁止。修复在原task分支追加提交或新task/<node>-fix-<n>，保留旧head证据。

托管worktree真实目录由工具返回；Git CLI备用位置D:agentKnowledgeWeaver.kwworktrees<node>。必须检查与主repo/其他checkout不重叠。删除/archive前核对分支引用、未提交/ignored文件与会话状态；本计划不授权自动删除未知工作区。
