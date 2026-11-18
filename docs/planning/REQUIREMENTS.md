# 需求、约束与可追踪验收

| ID | 必须实现 | Owner | 验证 |
|---|---|---|---|
| F01 | 四源结构解析；章节/符号/表格结构；parent/child chunk；文件版本/重复与增量 | B01 | 四源 gold fixtures；变更/删文件/重导入 |
| F02 | Entity/Relation/Evidence/Provenance；静态/抽取/推断区别；消歧 | B02 | 每条 accepted 关系有证据，错引文拒绝 |
| F03 | PG 事实库、Qdrant Dense、BM25/符号/实体索引；版本发布和删除 | B03 | 故障注入、旧快照继续可读、删除不泄漏 |
| F04 | 五路统一结果、RRF、rerank、可变 top_k | B04+B05 | 手算排序、证据不丢、通道单独运行 |
| F05 | 有界多跳与逐跳证据、跨来源关联与冲突 | B05 | 两跳、反向标记、环/高扇出预算 |
| F06 | SEGR、调查状态/动作反馈、重规划、动态停止 | B06 | 不同缺口有不同动作，预算与停止反例 |
| F07 | Benchmark、来源分组、指标、基线、消融 | B07 | 全矩阵可复跑，不接受只给均值 |
| F08 | 本地 API/UI、任务进度、引用、轨迹与实验展示 | B08 | 干净环境完整演示与错误路径 |
| N01 | Windows 原生业务 + Docker Desktop 基础设施 | P1 | Python 3.11/uv、服务健康/持久化 |
| N02 | 独立 Work/Worktree；并发≤3；本地集成且可恢复 | P2/Orchestrator | 两独立任务、故障回滚与崩溃恢复 |
| N03 | 内容/代码不成为工具指令；密钥/路径/版本隔离 | 全体 | 注入、路径逃逸、日志脱敏测试 |
| N04 | 全部事实输出可引用；不确定与无证据可拒答 | B06/B08 | claim 引用校验+人工语义支持评估 |

解析边界：PDF 首版只验收有文本层、常规阅读顺序、普通表格，页面/章节均尽力提取并记录 warning，不能承诺任意论文结构完美。DOCX 页码不稳定，使用正文 block/table/cell；Markdown 保留行号；Python 静态 CALLS 仅确定目标，动态调用标 unresolved。

增量定义：比较每文件原始 hash+parser/chunker/extractor fingerprint，未变文件复用不可变对象及 embedding cache，仅重算变化文件；删除更新版本成员。发布仍构造完整 generation 索引快照，10k chunks 上限下接受，不宣称跨存储增量事务。

重复内容按文本 hash 标 duplicate_group，不跨 source 丢原文定位。多份同源副本不应提高“独立来源”多样性收益。切块由结构决定，大小上限仅防超模型窗口；超大函数/表格按语句/行拆子块并保留 parent 指向。parent 默认不进排名，leaf 才作为统一检索单位。

时延/准确率以实验结果报告，不设置无依据的 99% 正确率或毫秒 SLA。API 默认本机 127.0.0.1；隐私输入外发须明确配置同意。中文英文均须进入模型评测和 Benchmark 子集。
