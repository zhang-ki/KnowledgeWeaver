# KnowledgeWeaver 本地使用与部署指南 (Windows)

## 1. 架构与服务边界

KnowledgeWeaver 本地产品由三部分组成（均绑定 `127.0.0.1` 回环地址）：
- **数据服务 (Docker Compose)**: PostgreSQL (`127.0.0.1:55432`) 与 Qdrant (`127.0.0.1:56333`)，配置见 `infra/compose.yaml`。
- **FastAPI 本地服务 (`kw serve`)**: 默认监听 `http://127.0.0.1:8000`，提供 `/healthz`、`/readyz` 及 `/v1/*` 能力。
- **Streamlit 本地 UI (`kw ui`)** 与 **CLI (`kw`)**: 均通过 `KW_API_URL` 调用 FastAPI `/v1/*` 接口。

> [!NOTE]
> 当前本地 Agent 默认使用 `policy=fixed`（或显式 `rule`），未加载训练后的 SEGR 权重。正式 Gold G3 与研究 Benchmark 评测状态仍为 `DEFERRED` / `BLOCKED`，产品交付状态为 `PRODUCT_READY_WITH_G3_DEFERRED`。

## 2. 必需环境变量

使用下方一键脚本时，环境变量会自动从 `D:\agent\KnowledgeWeaver\.kw\secrets\compose.env`、可选的 `D:\agent\KnowledgeWeaver\.kw\secrets\local.env`（模板见 `D:\agent\KnowledgeWeaver\scripts\local.env.example`）以及 `D:\agent\KnowSphere\.env` 自动装配，无需手工逐条设置。若手动运行 `kw` CLI，关键变量如下：

```powershell
$env:KW_DATABASE_URL = "postgresql://kw:<password>@127.0.0.1:55432/knowledgeweaver"
$env:KW_DATA_DIR = "D:\agent\KnowledgeWeaver\.kw\product_data"
$env:KW_ALLOWED_ROOTS = "D:\agent\KnowledgeWeaver\my_docs"
$env:KW_CORPUS_ID = "02f86e70-2d2e-4142-9389-3c1ac0b9bd86"
# 可选（启用 Dense 检索与本地 Q&A）
$env:KW_QDRANT_URL = "http://127.0.0.1:56333"
$env:KW_QDRANT_API_KEY = "<local-qdrant-key>"
$env:KW_EMBEDDING_BASE_URL = "http://127.0.0.1:11434/v1"
$env:KW_EMBEDDING_MODEL = "qwen3-embedding"
$env:KW_LOCAL_CHAT_URL = "http://127.0.0.1:11434/v1/chat/completions"
$env:KW_LOCAL_CHAT_MODEL = "qwen3"
```

## 3. 启动命令

### 一键启动 / 停止（统一入口均位于 `D:\agent\`，自动拉起 Docker Desktop 与容器）
```powershell
# 一键联合启动（位于 KnowSphere 目录：KS + KW + MQ Bridge + 全部容器）
D:\agent\KnowSphere\scripts\start-all.ps1

# 仅一键启动 KnowSphere（位于 KnowSphere 目录）
D:\agent\KnowSphere\scripts\start-ks.ps1

# 仅一键启动 KnowledgeWeaver（位于 KnowledgeWeaver 目录）
D:\agent\KnowledgeWeaver\scripts\start-kw.ps1

# 一键停止（保留所有数据库与存储卷数据）
D:\agent\KnowSphere\scripts\stop-all.ps1                   # 联合停止全部服务与容器
D:\agent\KnowSphere\scripts\stop-ks.ps1                    # 仅停止 KS 容器
D:\agent\KnowledgeWeaver\scripts\stop-kw.ps1               # 仅停止 KW 进程（加 -StopContainers 同时停容器）
```

### 轻量前台脚本（不含容器拉起）
```powershell
# 同时在前台启动 FastAPI (8000) 和 Streamlit UI (8501)
.\infra\start-local.ps1 -Mode all

# 仅检查环境与就绪度
.\infra\start-local.ps1 -Mode doctor
```

### 使用 `kw` CLI
```powershell
kw doctor
kw serve --port 8000
kw ui --port 8501 --api-url http://127.0.0.1:8000

# 知识源导入与检索
kw ingest --path D:\path\to\repo --kind python_repo --display-name "Sample Repo"
kw search "authentication token" --channel hybrid --top-k 10
kw investigate "How does token validation work?" --policy fixed --max-steps 6
kw investigation-status <run_id>
kw evaluate
```

## 4. FastAPI 接口一览

- `GET /healthz`, `GET /readyz`
- `POST /v1/sources`, `GET /v1/sources`, `DELETE /v1/sources/{source_id}`
- `POST /v1/ingestions`, `GET /v1/jobs/{job_id}`
- `POST /v1/retrievals`
- `POST /v1/investigations`, `GET /v1/investigations/{run_id}`
- `GET /v1/evidence/{evidence_id}?generation_id=...`
- `GET /v1/graphs?query=...&max_hops=2&max_edges=200`
- `GET /v1/experiments`
