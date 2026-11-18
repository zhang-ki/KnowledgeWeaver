# 阶段、开发和发布验收

## Phase0 文档验收

18份核心规划、九份Batch、P1/P2/P3任务、提示词、机器DAG/账本/模板全部存在；相对链接有效；公共schema字段、来源版本、graph端口和预算一致；Phase/B07两阶段依赖无环；所有真实状态未伪造；没有业务代码/安装/付费/新会话执行。通过只标P0 READY_FOR_REVIEW。架构审查由用户或其指定技术审查者对plan_hash确认，记录templates/REVIEW.md后P0 COMPLETED；不能自批开启P1。

## P1–P3 gates

P1：Windows3.11/uv/Git、精确依赖/镜像版本与digest、PG/Qdrant真实连通/健康/重启持久化、隔离备份恢复、秘密脱敏、测试工具基线。云provider选型可等待，不妨碍P1基础环境完成；P3 live gate不可跳过。
P2：两个独立可见Work会话/checkout/分支/提交；成功候选集成；故意失败候选不推进ref；冲突不覆盖；提交SHA和allowlist检查；writer并发锁；中断后journal恢复；实际消息投递与定时唤醒。任何关键项缺失P2保持BLOCKED，不得开始正式并行。
P3：至少一个文本PDF+一个Python仓库到真实PG/Qdrant/embedding，含一条人工可核验跨源两跳，真实LLM回答与引用、缺证据拒答、预算停止；DOCX/Markdown在B01正式扩展。原型只在experiments/vertical_slice，不等于B00–B06完成。

## 开发节点自动集成门禁

G1基线/分支/allowlist/契约合法；G2新功能单元和错误路径；G3schema/ID/引用闭合/过滤与generation一致；G4离线回归全绿；G5受影响PG/Qdrant真实service测试成功；G6交付与日志hash绑定实际head；G7候选merge后的完整回归通过、CAS推进integration并写journal。Live-model属于单独capability gate：无key不阻塞B00–B06离线实现集成，但不能声称真实模型验收；P3/B07.final/B08相应真实链路必须补齐。

执行标准命令：uv sync --frozen --group dev；uv run ruff check .；uv run mypy src；uv run pytest -m "not live_model"（包含可用的service tests，缺服务算失败/等待而非通过）。每测试记录命令、时间、Python/lock、exit_code、pass/fail/skip、artifact hash。Windows原生执行为必需；GitHub CI可选。固定工具门禁通过后Orchestrator自动集成，无需逐Batch手动PR或人工合并。

## Batch 特定验收

B00完整public models/ports/fakes/可用配置；B01四源定位+结构+父子块+文件增量；B02静态/LLM/规则区分、伪quote拒绝与保守对齐；B03全链发布/幂等/更新/删除/失败保旧；B04四路/RRF手算/rerank引用不丢/graph可注入；B05两跳证据闭合/过滤/反向/环预算；B06路由收益/状态反馈/硬软停止与策略对照；B07.prep人工Gold/分组冻结/指标手算；B07.final全部矩阵真实运行/费用与失败披露；B08四源完整演示/API/UI/错误恢复/实验展示一致。细则以对应Batch任务为准。

## 稳定main与研究结论

里程碑审查绑定integration SHA与plan_hash，批准后ff-only推进main；不得为通过测试删难题/隐藏skip。负实验结果可通过，只要设计公平且全部执行；缺组/假数据不可通过。Citation存在率与语义支持度分开；人工Gold与答案审查不能由CI代替。研究结论强度受样本数量与judge误差限制，不要求保证优于baseline。

最终项目完成必须P1–P3、全部Batch及RELEASE均COMPLETED，必需真实模型/实验gate全通过，人工队列清空或有已批准范围修订；无活动/待集成工作。Phase0报告和任何单个BLOCKED都不是项目完成。
