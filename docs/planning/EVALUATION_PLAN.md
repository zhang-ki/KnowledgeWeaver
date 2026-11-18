# Benchmark、策略训练与正式实验

## 数据与分组

正式MVP目标100题，single/multihop/cross_source/conflict/code各20；约各半中文英文，至少10%无答案题，包含PDF/DOCX/Markdown/Python。150题为扩展。每题人工核查Gold原文quote/hash/locator、claim_group、路径及答案；LLM生成题须人工复核才入正式集。来源许可与可发布范围入manifest，受限原文只存本地。

source family指同论文/版本/改写与对应代码仓库/派生文档的连通组，跨源相关资料合并同family。以family划分train/dev/test，目标30/20/50题、至少15个family（每split至少5）；具体分组可因family大小调整，但test≥40且每类≥6，必须在看系统结果前冻结并公开实际数量。若素材不足不随机拆同family凑数，应补数据或申请CR。train拟合收益，dev选参数，test只评估。

Gold按原文locator不依赖chunker。最终建立Gold span→leaf chunk映射，原文span与chunk引用范围有非空交集即候选，再人工/确定规则确认包含所需quote；重叠切块作为等价组，检索Recall按Gold证据组命中，另报严格chunk Recall以便对照。模糊映射与跨版本冲突阻塞，不删困难题。所有系统同一generation/corpus与parser/chunker/embedding配置。

## 策略拟合（只用train/dev）

B06实现SEGR policy载入与特征/日志格式；B07.final在train运行有界探索收集动作日志，不改业务代码。每train问题最多6状态，每状态最多8候选试运行（固定分层覆盖工具/top_k/hops），总调用预算单列。每个分支从同一状态副本执行，奖励以人工Gold覆盖增量标注；不得让一个分支结果泄漏给另一个分支。

拟合每动作族12维ridge与cost中位数，sample不足(<24条/族)则继续有限补采样或报告该族使用固定prior；正式SEGR若关键族均无足够样本则不宣称学到自适应收益。参数λ、λ_cost、τ仅在dev选择，保存weights/features/schema/train manifest hash。测试运行只做单query EMA更新，query结束清空，不跨test更新全局权重。训练费与推理费分别列出，不能只报便宜的推理。

## 九个对照系统

| ID | 行为 |
|---|---|
| bm25 | 单次BM25，统一生成器回答 |
| dense | 单次Dense，统一生成器 |
| hybrid_rrf | 固定BM25+Dense+Symbol+Entity，RRF |
| hybrid_rerank | 上一组+统一Cross-Encoder/API rerank |
| graph | Entity seed+独立Graph两跳，seed缺失计空，不偷接hybrid |
| fixed_agent | 固定轮次顺序hybrid→graph→一次expansion→rerank，硬预算约束 |
| rule | 仅问题意图/来源规则选择动作；无 learned gain/反馈EMA |
| llm_router | LLM读取相同state和候选选动作；schema/预算程序约束，决策tokens计费 |
| segr | 冻结的收益模型+在线query内EMA+成本/停止策略 |

除路由设计差异外，生成模型/prompt/verifier、输入语料、引用候选上限10、reranker可用性保持一致；没有需要的provider则系统标不可运行并阻塞最终矩阵，不能用另一模型静默替换。

预算设三档，所有系统共享每档硬上限：low=3轮/6工具/4 LLM/6000tokens/30s；standard=6/12/8/12000/60s；high=8/16/10/16000/90s（Phase5专用实验上限，产品默认仍standard）。训练和索引构建不计单query但单列。静态系统只用必要调用，其未用预算不能伪称花完；同时报告同cap与达到相近质量的实际花费。provider配置限制不得高于用户已授权金额。

## 十项单独消融（基于SEGR一次关一项）

A01无动态路由→固定动作序列；A02无graph；A03无关系语义证据验证→隔离测试仅移除关系是否受证据支持的检查，引用存在性/权限/版本硬约束保持；A04无expansion；A05无reranker；A06无充分性评估→不以coverage充分停止，其他停止保留；A07无动态停止→禁用U阈值/充分/无增益软停止，仍有动作耗尽和所有硬上限；A08去source相关features/多样性收益；A09无learned gain与EMA→公开固定prior；A10无cost项→λ_cost=0。说明A06/A07区别，不能仅改名字跑同一系统。

标准预算下全消融；九系统三预算全对照。每配置3次固定seed/相同题序随机化，温度/缓存/并发记录。API随机性不保证完全可重现，保存逐题响应摘要、usage和指纹。正式总run数=(9×3+10)×3=111套，按至少40个test题估算≥4440 query runs；先用dev估算成本并请求用户授权，预算不足则最终实验gate等待或经CR缩减，不暗中少跑。

## 指标的可计算定义

检索K=5,10,20。Precision@K=前K去重相关证据组数/K（不足K空位计0）；Recall@K=命中相关组/全部Gold组；MRR为首相关组倒数；nDCG用2^grade−1和log2(rank+1)，同组重复只算首次。严格chunk版指标另列，不混用分母。无答案题不计上述分母，单报拒答准确率/假引用率。

Evidence Recall=被返回证据覆盖的Gold claim_group数/全部应覆盖组；Citation Precision=真正支持其claim的引用数/所有输出引用数，人工支持标签；Citation validity=可解析原文比例，分开报告。应回答却无引用时precision/recall记0，正确拒答无引用标N/A且报告分母。

多跳准确率：multihop题中至少返回一条与Gold等价、方向/全部边证据正确的完整路径比例，另报各跳/路径覆盖；最短路径不自动算正确。Answer Correctness人工盲评0/1/2（错/部分/充分并处理冲突），归一化仅为展示。所有test Gold人工审核；系统答案抽样≥20%做双人复核，记录一致率与分歧；如单人评审披露限制并交技术负责人决定结论强度。

Token Cost包含分析/路由/改写/verifier/生成/重试，exact或estimated不可混淆；美元/人民币按价格快照计算，未知=null。Latency单列检索/策略/verifier和端到端p50/p95，concurrency=1与缓存冷/热分别记录，不能挑最快样例。

主比较为SEGR vs rule/fixed/hybrid_rerank在standard预算的Evidence Recall与Answer Correctness、实际tokens/latency；train/dev冻结后不得据test调参。按source family做paired bootstrap95%CI，测试family少时结论标探索性；附每题结果、失败模式与质量-成本Pareto图。无显著优势或负结果均可验收，不能只挑有利子集。

产物：dataset/model/policy/corpus/git hashes、hardware、pricing snapshot、config、seeds、per_query.jsonl、metrics.json、报告、失败列表、复算命令。大型原始日志gitignored，报告存hash和可访问本地路径；不能只有不可访问截图。
