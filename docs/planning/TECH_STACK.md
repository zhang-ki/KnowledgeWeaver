# 最小技术栈与 Provider 选择

| 选择 | 必要性与代价 | 不选/替代 |
|---|---|---|
| Python 3.11 + uv + Pydantic v2 | 统一 Windows 运行与 schema；P1 锁精确 Python patch、uv；B00 锁业务依赖 | 不混用系统 conda 3.12 与项目 .venv |
| stdlib ast / python-docx / markdown-it-py | 保留代码和文档结构，成本适中 | 不执行导入模块 |
| Docling PDF adapter | 首版需章节/页面/表格，结构模型有下载和 CPU 成本；OCR关闭，记录模型 revision | PyMuPDF 是更轻候选，但需审查许可与表格效果；pypdf仅文本不足以默认承诺结构/表格 |
| PostgreSQL + SQLAlchemy 2/Alembic/psycopg | 规范证据/版本/作业事务 | SQLite减运维但本项目已固定服务组合；不加第二事实库 |
| Qdrant + qdrant-client REST | 向量索引和过滤；需明确版本/维度 | 首版不启用 gRPC/集群 |
| rank-bm25 BM25Okapi | 10k chunks有界基线，简单可复算 | 不引入 Elasticsearch；中文tokenizer单独版本化 |
| NetworkX MultiDiGraph | 保留多关系与方向，2跳有界算法 | 不用 Neo4j/复杂图框架 |
| 轻量自建 Runtime + NumPy ridge | 可直接记录 SEGR 动作/反馈/预算，符合研究目标 | LangGraph 可提供恢复能力，但MVP已有PG状态和简单流程，暂不加依赖 |
| httpx 云API adapters | 云模型优先，显式超时/schema/usage | sentence-transformers 仅本地 fallback/离线模型可选extra；不默认下载大权重 |
| FastAPI + Streamlit | 本地交互与展示 | 不引入单独JS前端/Redis/Celery |
| pytest/Ruff/mypy | Windows可重复门禁 | 远端CI为可选增强，不是交付前提 |

版本策略：Python 3.11 系列；PostgreSQL固定候选 postgres:16.13-bookworm，Qdrant固定候选 qdrant/qdrant:v1.17.1。两者为有明确发布/标签依据的保守基线，不声称是当前最新或无安全问题。P1 拉取前查支持/安全公告，必要 patch 更新走记录化批准；通过实测后把 tag+digest、server/client、Python/uv/依赖记录到 environment.lock.json/uv.lock。正式配置禁止 latest 和浮动 major-only tag；不得编造 digest。Docling依赖/模型许可和Windows兼容也在P1小样例验证，不能提前称已可用。

Provider 三端口分开：generation、embedding、rerank。云API优先，兼容 /chat/completions 并不保证 embedding/rerank 端点或 JSON Schema 支持；分别检测。具体厂商/模型暂未选定，用户提供 endpoint/model/授权后在P1/P3验收，架构不依赖名称。

选型表每候选必填：endpoint允许范围、model/revision、中文英文小样本、JSON成功率、context/output限制、embedding维度/query指令、rerank分值方向/长度、实测p50/p95、并发/限流、超时/重试、usage准确性、官方价格链接与核查日期、每次调查预计费用。初始并发=1，模型超时15s，重试≤2；总调查60s优先。

真实模型请求必须用户授权与金额/token上限；不知道单价则cost=null，不填0；如用户要求硬货币上限而价格不可验证，暂停付费调用。FakeProvider由B00提供，可测试排序/错误/预算，不能用其测真实算法收益。受限离线模式仍能开发和验证大多数代码，真实模型验收单独列gate。

资料依据：[Docling安装](https://docling-project.github.io/docling/getting_started/installation/)、[Docling仓库](https://github.com/docling-project/docling)、[uv Python管理](https://docs.astral.sh/uv/guides/install-python/)、[Postgres官方镜像标签](https://hub.docker.com/_/postgres/tags?name=16.13-bookworm)、[Qdrant 1.17.1发布](https://github.com/qdrant/qdrant/releases/tag/v1.17.1)。版本是否适用于本机仍须实测。
