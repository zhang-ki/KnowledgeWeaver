# P1.python_toolchain — Python/uv 与 Docling 环境验证

记录版本：`KW-P0-2026-11-18`；计划 hash：`6ef838b43dc6a6a8d1680c8b9503e55fe7a63c6ca9c4238e4942acf8d495d1df`。本文件只记录独立子任务 `P1.python_toolchain`；不更改架构、Batch DAG、冻结计划或账本，不表示 P1 整体完成，也不启动 B00。

## 工作树与范围

- 实际工作树：`C:\Users\lenovo\.codex\worktrees\a843\KnowledgeWeaver`
- 分支：`task/p1-python-toolchain`
- 基线：`c8d2744188d089976a0be532da72fbf40b0b9c25`
- `head_commit` 在提交前留空；最终提交 SHA 由本任务最终回复和 Orchestrator 绑定。
- attempt id 未包含在分派提示中；未读取或写入 `.kw/runtime` /运行账本。

本次变更仅覆盖分派 allowlist：`.python-version`、`pyproject.toml`、`uv.lock`、`verification/environment/python/**`、本文与 `docs/deliveries/P1-python.md`。验证目录中的 `.gitignore` 将本地 `.venv`、Python runtime、uv bootstrap、缓存及模型权重排除在提交之外；这些文件均保留在工作树中。

## 本机盘点

| 工具 | 实测路径与版本 | 结果 |
|---|---|---|
| Windows | Registry `ProductName=Windows 10 Home China`，`DisplayVersion=25H2`，`CurrentBuild=26200`；`[Environment]::OSVersion=10.0.26200.0` | CIM `Win32_OperatingSystem` 查询返回拒绝访问；因此保留 Registry 原值，不推断产品名。 |
| 系统 `python` | `C:\Users\lenovo\anaconda3\python.exe`，Python 3.12.7 | 保持未修改。 |
| Python launcher | `py` | `Get-Command` 未找到。 |
| Git | `D:\git\Git\cmd\git.exe`，Git 2.51.0.windows.1 | 可用。 |
| PowerShell | PowerShell 7.6.9 | 可用。 |
| 系统 `uv` | `Get-Command uv` 未找到 | 使用验证目录中的 uv；没有改 PATH。 |
| Docker / `psql` / `pg_isready` | `Get-Command` 未找到 | 本子任务未启动或验证数据库服务。 |

项目内工具：uv 0.13.9 (`6b2d39dcc`, Windows x86_64 MSVC)；CPython 3.11.18 位于 `verification/environment/python/runtimes/cpython-3.11.18-windows-x86_64-none/python.exe`；隔离环境位于 `verification/environment/python/.venv`。Python runtime、uv bootstrap、venv、下载缓存及 Docling 模型均在 `verification/environment/python/` 下，未安装或修改全局 Conda，也未更改 ExecutionPolicy。

## 项目环境与锁

`.python-version` 精确固定为 `3.11.18`。`pyproject.toml` 声明 `requires-python = "==3.11.*"`、Docling `>=2,<3`，并将 psutil、pytest、Ruff 作为验证开发依赖；`[tool.uv].package = false`，没有创建业务 package 或 `src/`。本任务不需要 mypy，因此未安装或配置。

最终锁定包括 129 个平台条件包；本机 Windows 同步安装 108 个包。实测版本：

| 包 | 版本 |
|---|---:|
| docling | 2.150.1 |
| docling-core | 2.107.1 |
| docling-parse | 7.26.0 |
| docling-ibm-models | 4.0.4 |
| torch | 2.15.0 |
| psutil | 7.2.2 |
| pytest | 8.4.2 |
| Ruff | 0.17.5 |

`uv.lock` SHA-256：`4c41a9ab7ef2f51b0cf41c6fcfe6a3515fefd6e2a4e588415e7ad7ca605b9a5e`。`uv lock --offline` 后 `uv sync --offline --frozen` 成功，表明当前 lock 与项目依赖声明一致且本地锁定环境可重复同步。

## Docling 本地 PDF 探测

样例由 `verification/environment/python/validate_docling.py` 使用 Python 标准库生成；单页、763 bytes、仅含合成文本层，不含用户资料或第三方文档。样例 SHA-256：`e85a24d16587526e5e5b22168186bbf6d9be67297258f683d7a9b5fb40056814`。程序使用 Docling `DocumentConverter`，显式关闭 OCR 和 remote services；`table_structure` 关闭以验证普通文本解析并避免重试已失败的准确表格模型下载。

实测结果（2026-11-18，Asia/Shanghai）：

- `ConversionStatus.SUCCESS`；识别 1 页；预期 marker `orchard-247` 被解析到。
- `table_count=0` 仅表示本文本探测未启用表格结构抽取，不代表表格识别通过。
- Docling 转换调用 21.132 秒，探测脚本总计 34.397 秒；外层命令从 21:25:54 到 21:26:34，退出码 0。
- 用 psutil `memory_info` 测量：转换前 working set 24.77 MiB；转换后 784.90 MiB；进程 peak working set 823.36 MiB；相对转换前的峰值增量 798.59 MiB。该峰值包含模型加载和导入开销，不是纯解析算子用量。
- 缓存模型解析版本：layout repo `docling-project/docling-layout-heron` 请求 `main`，实际缓存 commit `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8`；table repo `docling-project/docling-models` 请求 `v2.3.0`，实际缓存 commit `fc0f2d45e2218ea24bce5045f58a389aed16dc23`。layout 请求的 `main` 是浮动引用；本任务记录了运行时解析 SHA，但不修改锁外的模型配置。
- Docling 报告两个 deprecated 警告：`force_full_page_ocr` 配置字段，以及 `TableItem` 图片相关字段。未观察到解析失败警告。
- 使用 `HF_HUB_OFFLINE=1` 完成最终探测；不调用模型 API、不上传 PDF，也未配置或请求任何凭据。

表格模型失败记录：一次 `table_structure=True` 尝试下载 TableFormer accurate 权重时出现 read timeout、SSL EOF，随后缓存目标路径 `FileNotFoundError`，命令退出码 1。该次发生时间未被命令日志记录；模型下载通道已停止重试。表格抽取兼容性仍是未通过/未验证 gate。保留了本机已有模型缓存，未将模型权重加入提交。

## 配置诊断与脱敏

`verification/environment/python/validation_safety.py` 仅为隔离验证辅助：缺少设置时给出只含设置名的清晰错误；日志清理覆盖常见 `api_key`、token、password/secret、Bearer 和 URL user/password 形式。测试中仅使用明显虚构的样例字符串，无真实凭据、连接串或 API 配置。

## 可复现命令

在仓库根目录，用 PowerShell 执行。全新机器先用系统 Python 将固定版本 uv 装到 allowlist 内，再由 uv 安装项目专属 Python；pip 使用 `--target`，不会写入全局 Conda site-packages。下列 bootstrap 是复现实测版本的命令；本机实际安装时未把 uv 版本写入 `uv.lock`，安装结果为 0.13.9。

```powershell
python -m pip install --disable-pip-version-check --no-warn-script-location --target verification/environment/python/tools/uvsite --index-url https://pypi.org/simple uv==0.13.9
$uv = ".\verification\environment\python\tools\uvsite\bin\uv.exe"
& $uv python install 3.11.18 --install-dir verification/environment/python/runtimes --cache-dir verification/environment/python/cache
$env:UV_PYTHON_INSTALL_DIR = (Resolve-Path verification/environment/python/runtimes).Path
$env:UV_CACHE_DIR = (Join-Path (Get-Location).Path verification/environment/python/cache)
$env:UV_PROJECT_ENVIRONMENT = (Join-Path (Get-Location).Path verification/environment/python/.venv)
& $uv lock --offline --python 3.11.18 --cache-dir $env:UV_CACHE_DIR
& $uv sync --offline --frozen --python 3.11.18 --group dev --cache-dir $env:UV_CACHE_DIR
& $uv run --offline --frozen --group dev --cache-dir $env:UV_CACHE_DIR python --version
& $uv run --offline --frozen --group dev --cache-dir $env:UV_CACHE_DIR ruff check verification/environment/python/validate_docling.py verification/environment/python/validation_safety.py verification/environment/python/tests
& $uv run --offline --frozen --group dev --cache-dir $env:UV_CACHE_DIR pytest -q verification/environment/python/tests
$env:HF_HOME = (Join-Path (Get-Location).Path verification/environment/python/models/hf)
$env:HF_HUB_CACHE = (Join-Path $env:HF_HOME hub)
$env:HF_HUB_OFFLINE = "1"
$env:HF_HUB_DISABLE_TELEMETRY = "1"
& $uv run --offline --frozen --group dev --cache-dir $env:UV_CACHE_DIR python verification/environment/python/validate_docling.py --sample verification/environment/python/fixtures/plain_text_sample.pdf --result verification/environment/python/results/docling_probe.json
```

命令时间、退出码和输出摘要记录于 `verification/environment/python/results/p1-python-validation.json`；探测结果见 `verification/environment/python/results/docling_probe.json`。最终提交后由本任务在提交 SHA 上重跑 Ruff/pytest，最终回复将报告该 SHA 与结果。

## 迭代中失败与处置

- 初次在受限执行环境下载 uv 时 DNS 解析失败，pip 退出码 1；获准后从公开 PyPI 将 uv 0.13.9 安装到项目内工具目录，退出码 0。
- 首次递归 lint 把本地 uv bootstrap 源码也纳入检查而失败；最终 lint 命令只检查验证脚本与测试，退出码 0。
- 首次 pytest 收集未包含仓库根导入路径而失败；设置 pytest `pythonpath=["."]` 后，最终安全测试通过。
- 首次直接运行 Docling 脚本的包导入路径错误已修正；模型 Xet 下载曾无进度并被中断，改为标准下载后遇到上述 TableFormer accurate 超时。使用缓存布局模型、离线且关闭 table structure 的普通文本探测成功。
