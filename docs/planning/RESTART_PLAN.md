# 从零启动路线与阶段门禁

文件名沿用户给定目录保留 RESTART_PLAN；内容是首次正式启动计划。目标 D:agentKnowledgeWeaver。不存在要找回的旧实现；不以任何历史 Commit/Batch/会话作为当前依赖。

| 阶段 | 固定节点 | 输入门禁 | 完成证据 |
|---|---|---|---|
| Phase 0 | P0 | 最新需求 | 全部资料落地、互相一致、DAG 无环→READY_FOR_REVIEW；技术负责人/用户指定审查者批准后 COMPLETED |
| Phase 1 | P1 | P0 COMPLETED + 安装/系统动作逐项授权 | Windows 工具链、双数据库、隔离测试环境、锁版本、备份恢复、Git 基线；真实环境证据 |
| Phase 2 | P2 | P1 COMPLETED | 独立会话/Worktree/提交/本地集成/失败恢复/持久账本/定时唤醒全部实测 |
| Phase 3 | P3 | P2 COMPLETED | 隔离纵切片：PDF+Python 经真实服务/模型闭环；来源、路径与回答可核查 |
| Phase 4 | B00–B06 与 B07.prep | P3 COMPLETED | 正式实现与合同、离线回归、本地集成；B07.prep 不等于最终实验 |
| Phase 5 | B07.final | B07.prep+B04+B05+B06 INTEGRATED | 正式基线/消融/成本实验全部可复算，人工评估完成 |
| Phase 6 | B08、RELEASE | B06+B07.final INTEGRATED | API/UI/演示/最终审查，通过后稳定 main；公开发布需单独授权 |

Phase 4 不要求 B08 提前实现；B07 以 prep/final 两阶段跨 Phase 4/5，B08 对应 Phase 6。这样阶段序列不会与 B00–B08 互相等待。P1/P2 是工程准备，P3 是验证性原型，不提前完成正式 Batch。

P1：创建新 Git repo 的动作发生在本阶段授权后，main 仅保存受审查规划与工具基线，再建立 integration。P2 在 probe/* refs 上验证，测试用代码只在 verification/，不进业务目录。P3 只在 experiments/vertical_slice/，不被生产模块 import。B00 按冻结合同新建正式模块；可参考 P3 的经验证样例，不能将未审查原型直接算正式交付。

阶段结束判据是证据与门禁，不是会话结束。P1/P2/P3 内某项外部阻塞时完成同阶段的其他独立项；不能因此越过阶段门禁启动业务 Batch。无 API key 可完成离线工程，P3 的真实模型链路门禁保持等待。

所有安装、账户授权、收费调用、系统配置和不可逆动作进入人工队列；日常项目内提交/本地集成在用户启动对应执行阶段并授权该阶段流程后执行。当前仅创建文档，Git 保持未初始化。
