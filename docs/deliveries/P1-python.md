# P1.python_toolchain 交付

- task_id/batch_id/attempt_id/instance_id/plan_version/plan_hash：`P1.python_toolchain` / `P1` / 分派未提供 / `887b0733-5d66-4cfc-b56d-dec7555daeaa` / `KW-P0-2026-11-18` / `6ef838b43dc6a6a8d1680c8b9503e55fe7a63c6ca9c4238e4942acf8d495d1df`
- 实际 worktree/branch/base_commit/head_commit：`C:\Users\lenovo\.codex\worktrees\a843\KnowledgeWeaver` / `task/p1-python-toolchain` / `c8d2744188d089976a0be532da72fbf40b0b9c25` / 提交前留空；最终提交 SHA 由任务最终回复记录。
- rollback ref：`refs/codex/rollback/p1-python-toolchain-c8d2744` → `c8d2744188d089976a0be532da72fbf40b0b9c25`。
- 目标：验证项目专属 Python 3.11/uv 工具链、锁定依赖、Docling 普通文本 PDF 离线解析和配置诊断；不实现业务包，不启动 B00。

## Allowlist 文件

- `.python-version`：固定 Python 3.11.18。
- `pyproject.toml`、`uv.lock`：Docling 与最小验证工具依赖；`package=false`。
- `verification/environment/python/.gitignore`：忽略本地 runtime、uv bootstrap、venv、缓存和模型下载文件；没有删除这些目录。
- `verification/environment/python/validate_docling.py`、`validation_safety.py`、`tests/test_docling_pdf.py`、`tests/test_validation_safety.py`、`fixtures/plain_text_sample.pdf`、`results/docling_probe.json`、`results/p1-python-validation.json`：隔离探测、测试和证据。
- `docs/implementation/P1-python.md`、本文：实现记录与交付。

## 测试与验证

最终预提交复核在 Windows PowerShell、项目内 CPython 3.11.18/uv 0.13.9、离线缓存环境中执行。详细时间与输出摘要见 `verification/environment/python/results/p1-python-validation.json`（SHA-256 `e2dcfb59f383e9967ae658c7ac5b54d141ca7b6e99ee317693f4ac7e3c8ce049`）。

表中 `uv` 指本地可执行文件 `verification/environment/python/tools/uvsite/bin/uv.exe`；项目没有依赖全局 uv 命令。

| 命令 | 退出码 | 时间（Asia/Shanghai） | 结果 |
|---|---:|---|---|
| `uv lock --offline --python 3.11.18 --cache-dir verification/environment/python/cache` | 0 | 2026-11-18 21:31:38 | 解析 129 个锁定包。 |
| `uv sync --offline --frozen --python 3.11.18 --group dev --cache-dir verification/environment/python/cache` | 0 | 2026-11-18 21:31:38 | 本机 108 个包已锁定同步。 |
| `uv run --offline --frozen --group dev python --version` | 0 | 2026-11-18 21:31:38 | Python 3.11.18。 |
| `uv run --offline --frozen --group dev ruff check verification/environment/python/validate_docling.py verification/environment/python/validation_safety.py verification/environment/python/tests` | 0 | 2026-11-18 21:31:38–21:31:40 | All checks passed。 |
| `uv run --offline --frozen --group dev pytest -q verification/environment/python/tests` | 0 | 2026-11-18 21:31:40–21:32:19 | 3 passed（含安全配置与 Docling PDF 探测）。 |
| `uv run --offline --frozen --group dev python verification/environment/python/validate_docling.py --sample verification/environment/python/fixtures/plain_text_sample.pdf --result verification/environment/python/results/docling_probe.json` | 0 | 2026-11-18 21:25:54–21:26:34 | Docling `ConversionStatus.SUCCESS`；1 页；预期文本存在；OCR/远程服务关闭。PDF SHA-256 `e85a24d16587526e5e5b22168186bbf6d9be67297258f683d7a9b5fb40056814`。 |

Docling 探测报告 SHA-256：`328718ef6b793aec6dceafd84d7df4f42fed838689c63dd9e2dabca16cbe417a`。uv.lock SHA-256：`4c41a9ab7ef2f51b0cf41c6fcfe6a3515fefd6e2a4e588415e7ad7ca605b9a5e`。其他关键 artifact hash 见实现记录。

## Provider、限制与未完成 gate

- 测试仅使用本地合成 PDF；不使用用户资料、云模型 API、API credentials、数据库服务或 FakeProvider。
- Docling 表格结构模型下载失败（accurate 文件 read timeout/SSL EOF，随后缓存路径错误，退出码 1）；最终纯文本解析关闭表格结构。因此表格抽取 gate 未验证，不能据此宣称 Docling 表格兼容通过。
- Docling 布局模型的配置请求浮动 `main`，报告记录本次解析出的 commit `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8`；未授权修改 `environment.lock.json`，模型 revision 尚未纳入仓库锁。
- Windows Registry 返回 `Windows 10 Home China`/25H2/build 26200；CIM 查询被拒绝访问。Docker、psql、pg_isready 未安装或未找到；数据库连通/备份恢复不属于本子任务，状态未验证。
- 下载依赖前首次 DNS 失败（pip exit 1），通过项目内重试成功；本任务未更改 Conda、系统 PATH 或 ExecutionPolicy。
- 测试日志绑定本次候选工作树与基线 SHA；提交后的验证结果及实际提交 SHA 在最终回复中补报，交由 Orchestrator 将报告与提交关联。

## Artifact 与申请

实现记录：`docs/implementation/P1-python.md`。核心 artifact：`uv.lock`、`verification/environment/python/fixtures/plain_text_sample.pdf`、`verification/environment/python/results/docling_probe.json`、`verification/environment/python/results/p1-python-validation.json`。完整 SHA-256 见上文及实现记录。

申请 `READY_FOR_REVIEW`。不自行写 `INTEGRATED`/`COMPLETED`，不更新运行账本，不合并或 cherry-pick。
