# Windows 原生开发环境

日常工作目录 D:agentKnowledgeWeaver；所有命令 PowerShell，Python代码、uv、Git和测试在 Windows运行。Docker Desktop可使用WSL2内部后端，不需要Ubuntu terminal或WSL项目目录。

2026-11-18只读检查：python可见为Anaconda Python3.12.7；Git2.51.0.windows.1；PowerShell7.6.9；OS报告10.0.26200.0，CIM名称查询受限。当前PATH未发现uv/docker，无法据此断言未安装。未检查/启动数据库，未创建venv，未安装任何软件。

P1先盘点已有安装与Windows11版本/虚拟化条件，安装或系统变更先授权。Python3.11使用项目专属uv-managed解释器，不卸载或改用户conda。uv可自动下载解释器，因此未授权时设置 UV_PYTHON_DOWNLOADS=never；授权后使用 uv python install 3.11，再 uv python pin 精确patch，uv venv --python 3.11。禁止更改全局ExecutionPolicy；日常用 uv run 无需激活脚本。

P1仅建立验证工具基线（pyproject、uv.lock、pytest/Ruff/mypy、环境探测和服务ping）；B00才建立正式业务package/ports。Phase2 probe测试与Phase3原型各有独立目录。所有工作树独立.venv；uv下载缓存可共享，环境不能共用。

常用命令契约（实现后在仓库根执行）：
```powershell
uv sync --frozen --group dev
uv run python --version
uv run ruff check .
uv run mypy src
uv run pytest -m "not live_model"
```
P1/P2尚无src时mypy针对verification，B00后针对src；禁止以“没有测试收集”退出码当通过。marker分unit/contract/service/live_model/e2e；service用真实PG/Qdrant且独立测试命名空间，live_model按授权单跑。API未配不跳过离线测试。

.env.example是配置清单，复制成.env只在用户授权执行阶段；真实密码/Key由用户或本地安全随机生成，不打印。根.env不共享到Work提示词；每worktree使用指向受控本地配置的显式路径，日志不含绝对用户目录/凭据。

目录：data/raw、data/indexes、data/cache、artifacts、backups均gitignored；.kw/runtime是主项目共享调度账本，.kw/worktrees下各独立checkout，.kw/secrets可存仅本机配置。每测试run使用唯一PG schema与Qdrant collection前缀test_<run_id>；清理仅这些资源，禁止清空开发库。私有原文不进入repo，fixtures用许可清楚的小样例。

完成P1需要真实解释器/依赖锁、服务连通/健康/重启保留、一次隔离备份恢复、配置缺失/脱敏测试与evidence报告。模型凭据缺失是model gate等待，不妨碍离线项；P3真实链路仍须凭据。
