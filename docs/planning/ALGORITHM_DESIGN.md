# SEGR：来源感知的证据增益路由

## 假设与可证伪贡献

H1：来源匹配与当前证据缺口共同预测下一次检索的边际收益，优于固定全路执行或仅看问题类型的规则路由。H2：观察本次新增证据后更新动作价值，能在相同硬预算下改善 Evidence Recall/claim coverage 或以较少 token 达到相近质量。H3：有证据的图扩展主要帮助多跳/跨源问题，单源短问题可能无收益。

贡献候选是可复现的状态表示、收益标注与代价约束策略；不预设 H1–H3 成立。用固定协议检验，负结果可交付。

## 输入、状态与输出

输入 Query、已冻结 QueryContext、用户硬 Budget、可用工具/来源目录、策略参数指纹。输出 Action 或 StopDecision；最终 Answer 由同一生成器基于已收集证据生成。

State 包含：最多5个 SubQuestion（最初问题分解，后续拆分有记录）、每个 facet 的所需 source type、已证实/未解决/冲突状态、EvidenceLedger、独立 source family、paths、历史 query/tool/top_k、剩余预算、连续无增益次数、候选动作与失败计数。整个调查固定 generation。

分解一次由结构化 LLM 完成；离线 fake 只供行为测试。source_type 是问题要求/候选相关性，不预设所有问题都应跨来源。模板符号识别与输入文件类型提供确定性特征。模型分解或语义评分失败时使用保守“未解决”标记，不默认为充分。

## 证据支持与观测奖励

对证据 e 与子问题 f，先做硬校验 v(e)：真实 generation、可解析 quote、允许 source、图路径每跳闭合。合法不代表语义支持。
用统一的结构化 verifier（所有对照策略共享同模型、prompt、预算）标支持度 p(e,f)∈{0,0.5,1} 与 conflict；结论按本次引用片段评估，不允许自报数值当校准概率。代码 locator 类可由确定性符号匹配提供支持标签；语义无法确认记0。Static provenance 是可复现方法而不是“所有事实为真”的保证。

C_t=平均_f max_e p(e,f)，只在 v(e)=1 的证据上计算；D_t=所需 source type 中有支持证据的比例（无明确多源要求时权重置0）；K_t=有明确相反证据却未被回答标注的 facet 比例。冲突“处理”指有双侧引用并在结果中标注，不指确定谁真。重复 quote/duplicate_group、相同文档副本不重复增加 coverage/source family。

动作的可观测收益 y=clip[0.7ΔC+0.2ΔD+0.1ΔK_resolved,-1,1]。成本单独计罚而非重复放进 y；Verifier 和 query rewrite 的调用成本算入动作。训练目标由人工 Gold 在 train split 的轨迹标注，在线使用 verifier 代理；两者分开报告偏差，禁止在 test 使用 Gold 来决策。

## 动作集合与参数

动作 a=(tool,facet_id,query_variant,top_k,hops,rerank)。tool∈bm25/dense/symbol/entity/graph；top_k∈{5,10,20}；graph hops∈{1,2}；每 facet 原 query 和最多一次 expansion；rerank 仅对已收集候选≤40重排。每轮最多30候选，优先处理未解决 facet；按剩余预算剪枝。没有实体 seed 不生成 graph 动作，未配置 provider 不生成依赖它的动作。

符号查询选择具体 qualified name，文档查询用原文/扩展；rerank 是独立候选动作，只重排证据池，不凭空提高证据覆盖。它的奖励来自同一固定展示上限（最终最多10条引用候选）内支持证据覆盖变化，避免把单纯改分数算新知识。

## 收益预测与决策（MVP 不用复杂搜索/强化学习）

使用按动作族分组的线性 ridge 模型预测增益。feature vector x 为12维：bias、未解决比例、来源匹配、已有支持覆盖、当前冲突比例、独立来源缺口、该族最近真实增益 EMA、query lexical specificity、exact symbol flag、seed 存在、已执行同族次数归一化、剩余预算比例。明确 clip 到[0,1]，模型与 tokenization 指纹固定。

Phase5 在 train 数据的有限动作日志拟合 θ=(XᵀX+λI)⁻¹Xᵀy，λ∈{0.1,1,10}仅在 dev 选；禁止运行时下载训练集/把 test 更新到全局参数。每个 query 内，用观测 y 对该动作族 EMA 做 α=0.5 更新；query 结束清空 EMA，保持 test 查询相互独立。

g_hat=clip(0.8·θ_aᵀx+0.2·EMA_a, -1,1)。冷启动无模型用预先公开规则 prior（exact symbol 0.6，来源匹配0.4，其余0.2），标 policy_profile=prior，仅用于 P3/开发；正式 adaptive 实验必须报告 ridge 模型，不能以 prior 冒充已学策略。

cost(a)=0.5·estimated_tokens/remaining_tokens + 0.5·estimated_ms/remaining_ms。估算来自 train 的动作族/参数中位数，冷启动使用保守上限；未知云端货币价格不进入优化，最终费用记 null。U(a)=g_hat−λ_cost·cost(a)−0.1·duplicate_risk，λ_cost∈{0.1,0.3,0.6}只在 dev 选择。取可执行 U 最大值，tie 按稳定 action_key；不进行隐式随机探索。

## 闭环与停止

Plan → Retrieve → Observe（去重/quote/路径校验、计量）→ Evaluate（更新支持矩阵与收益）→ Replan。每轮保存候选特征、预测收益/成本、所选动作、真实收益、剩余预算与 reason，不保存私有思维链。

硬停止优先：deadline、token、tool/LLM call、轮次、取消；每调用前预留，重试最多2次且全计费。软停止：全部 facets 支持≥0.8且冲突已披露；或 best U≤τ 且至少完成一次检索；或连续两次无新证据；或所有可用动作耗尽。τ∈{0,0.05,0.1}在 dev 选择。停止但不足时输出 insufficient 与缺口。无初次检索不能仅凭 prior 直接 answered。

## 开销与失败条件

d=12,A≤30：每步评分 O(A·d)，特征聚合 O(E·F)，F≤5，E≤200；图 O(V_seen+E_seen) 受边预算限制；训练每动作族 O(Nd²+d³)，无需 GPU。LLM verifier 与 reranker 往往是主要额外耗时，必须计入所有策略相同预算，单列其开销。

失败可能：train 小导致收益模型不稳；verifier 奖励与真实答案正确性不一致；多样性偏好引入无关来源；成本估计失准；实体错对齐带来错误图；短问题中决策开销超过节省。相同硬预算下报告质量/成本 Pareto，不能只挑质量或只报检索阶段时间。

基线和消融见 EVALUATION_PLAN；需要单独移除 source features、gain learning/EMA、成本项以及用户要求的七项能力。无动态路由的对照固定动作序列，其他组件不变。移除关系证据验证只在隔离实验允许不受支持关系进入候选，仍保留引用存在性/权限/预算硬校验，禁止将该模式用于产品回答。
