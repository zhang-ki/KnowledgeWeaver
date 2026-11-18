# P1 Docker Desktop 与数据库环境实现记录

- task_id：P1.docker_services
- plan_version：KW-P0-2026-11-18
- plan_hash：6ef838b43dc6a6a8d1680c8b9503e55fe7a63c6ca9c4238e4942acf8d495d1df
- 分支 / base：task/p1-docker-services / c8d2744188d089976a0be532da72fbf40b0b9c25
- 状态：PARTIAL — 已按批准 CR 配置 PostgreSQL + Qdrant 双服务 Compose；Docker Engine、服务、持久化与备份恢复门禁尚未通过。

## 完成的离线准备

- 重新核对专属工作树：指定分支、派发 base SHA、起始干净状态均匹配；未更改 main 或其他工作树。
- 读取冻结计划、P1 门禁、Compose 参考模板及交付模板。
- 主机盘点记录在 verification/environment/docker/host-inventory.json。系统版本 registry 值为 10.0.26200.0 / 25H2 / build 26200 / UBR 9974，ProductName 为 Windows 10 Home China，架构 AMD64。C 盘可用约 69.3 GB，D 盘可用约 202.3 GB。系统名称存在不一致，不能据此确认受支持的 Windows 11 版本。
- Docker CLI、Compose CLI、Desktop 安装路径和 com.docker.service 在 20:46 的初始盘点中均未发现。后来 Orchestrator 说明 `$env:LOCALAPPDATA` 被重定向，因此此前按该值查找安装路径的检查不适用于实际用户目录。WSL 的 version/list 查询退出码为 1、status 为 50；读取 Windows 可选功能状态要求管理员权限。固件虚拟化无法通过 CIM/systeminfo 可靠核验。
- Docker Desktop 官方 release notes 列出 4.99.0；官方 checksums 文件列出的 Windows x64 installer SHA-256 为 a5e299131c9c53d60f3f752b162078945eb5faf6eedcf0be20a214c6e6cea444。下载文件 SHA-256 与官方值一致、checksum listing 匹配、Authenticode 状态为 Valid。较早的即时版本观察（13:22:48Z）为 NOT_INSTALLED 且安装命令退出码为空；之后 Orchestrator 报告实际 per-user 安装路径中的 Docker Desktop.exe 版本为 4.99.0.245822 且签名有效，直接运行该路径的 docker.exe --version 得 Docker 29.9.3（exit 0），docker compose version 得 v5.7.0（exit 0）。Orchestrator 同时报无 Docker 进程、Engine 未启动；该报告没有提供实测时间。没有接受许可、启动 Desktop、修改 Windows 功能、更新 WSL 或重启。详见 verification/environment/docker/docker-desktop-install.json 与 docker-install-review.json。
- 2026-11-18T13:20:50Z 的协调器报告是较早状态快照：当时无提升令牌，Virtual Machine Platform 未确认，尚未运行无发行版 WSL 启用脚本，也未安排重启。随后 Orchestrator 报告用户授权后运行过管理员 WSL 无发行版启用脚本，退出码 0，但输出/后续状态出现 Wsl/E_ACCESSDENIED；Get-WindowsOptionalFeature 仍要求提升，RunAs 只读复核被拒。功能状态仍未确认，未重启。此项是 Orchestrator 报告，实际尝试时间未提供；当前需要本机管理员确认，不能断言已启用或未安装。见 verification/environment/docker/docker-install-review.json。
- Qdrant 1.17.1 Docker Hub 页面给出 Linux/amd64 manifest sha256:cd3e42737c684ee516ae5533218be93fd5288f41d0a466ed18dbdc22ef52a000 和 multi-platform index sha256:94728574965d17c6485dd361aa3c0818b325b9016dac5ea6afec7b4b2700865f。来源是 registry 页面，不代表本地 pull 已验证。
- CR-P1-POSTGRES-16.16 已由用户在 Orchestrator 会话批准，决定记录于 runtime event 29。批准仅限 `postgres:16.16-bookworm` 的 linux/amd64 平台 manifest digest，不改 PostgreSQL 16 主版本、冻结双服务架构或接口。详见 CR 文件。
- infra/compose.yaml 现包含 PostgreSQL 与 Qdrant 两个服务，并分别锁定已批准的 PostgreSQL 与既定 Qdrant amd64 platform manifest digest。PostgreSQL 映射仅为 127.0.0.1:55432 → 5432，数据库 knowledgeweaver、用户 kw、密码由必填的 KW_POSTGRES_PASSWORD 环境变量注入，使用 pg_data 命名卷及镜像内 pg_isready healthcheck。Qdrant 仅映射 127.0.0.1:56333 → REST 6333；主机不映射 6334 或 6335。Qdrant key 只通过 KW_QDRANT_API_KEY 环境变量注入，不写入仓库。两个服务仅接入 Compose project-local services network。
- verification/environment/docker/qdrant.production.yaml 明确 grpc_port: null 与 cluster.enabled: false。官方文档说明 null 会关闭 Qdrant gRPC；官方 GHSA-frc7-44gm-99qq 说明受影响的旧版本只在 distributed deployment 启动内部 gRPC 端口，single-node deployment 不启动该端口。保持集群关闭是 1.17.1 基线的安全前提。
- 当前安全公告核验：Qdrant v1.20.2 于 2026-11-16 发布；GHSA-frc7-44gm-99qq 标记 <1.20.0 有风险，但明确 single-node 不启动内部 6335。GHSA-3gph-6c29-p29v 影响 1.18.0 至 <1.19.2，不包括 1.17.1。GHSA-f632-vm87-2m2f 的受影响版本为 >=1.9.3 且 <1.15.6，不包括 1.17.1。没有升级冻结的 1.17.1；后续升级仍需记录化批准。
- 已准备 Qdrant Windows readiness + 隔离 collection create/write/scroll/delete 检查，以及 PostgreSQL Windows psycopg SELECT 1 检查。脚本不会打印 key、DSN 或驱动响应详情。
- environment.lock.json 记录两个镜像的 tag、linux/amd64 manifest 与 index digest、registry 证据及 CR 批准来源；PostgreSQL/Qdrant 本地 pull 与 runtime 状态均为 NOT_RUN。本任务没有拉取或运行任一镜像。
- 本地离线检查日志 verification/environment/docker/offline-validation.log，SHA-256 为 98feaf5666d4c38ee45ba4e48fb62ce5d2126b125cdfcd842766cb0293b882d7：3 个 JSON 文件解析通过、2 个 PowerShell AST 解析通过、Compose 源文本安全护栏通过、凭据模式/用户绝对路径/尾空白扫描通过、git diff --check 退出码 0。
- 配置缺失分支已分别实际调用：Qdrant 检查在缺少 KW_QDRANT_API_KEY 时退出码 2 并打印 NOT_RUN，PostgreSQL 检查在缺少 KW_POSTGRES_DSN 时退出码 2 并打印 NOT_RUN；两次输出均未包含任何 key 或 DSN。它们验证的是安全缺配置路径，不是服务通过。
- 2026-11-18 21:13:35 +08:00 / 13:13:35Z 检查 Docker CLI 未发现；docker compose config --quiet 当时未运行。之后 Orchestrator 确认 CLI/Compose 可用，但本工作会话没有运行 Compose config 或启动 Engine/服务。离线结构护栏不等于 Compose spec 解析通过。

## 尚未执行 / 阻塞

| 检查项 | 状态 | 原因 |
|---|---|---|
| Docker Desktop 与 CLI/Compose 二进制版本 | PASS — coordinator-reported Desktop 4.99.0.245822, Docker 29.9.3, Compose v5.7.0; signature valid | verification/environment/docker/docker-desktop-install.json |
| Docker Subscription Service Agreement | USER_ACTION_REQUIRED | 首次启动必须由用户阅读并决定接受；本会话不替用户接受 |
| Windows / WSL / BIOS 虚拟化前置条件 | WAITING_LOCAL_ADMIN_CONFIRMATION | 用户已授权；管理员 WSL 无发行版脚本 exit 0，但出现 Wsl/E_ACCESSDENIED；Windows 功能状态仍无法读取，RunAs 只读复核被拒；VMP/OS identity 未确认，未重启 |
| 当前双服务 Compose 静态解析 | NOT_RUN（本 Work 会话） | Orchestrator 将在隔离候选中使用本地 ephemeral env file 执行 config --quiet；本会话不调用 Docker CLI/API |
| Docker Engine / Qdrant pull、/readyz、读写/删除 | NOT_RUN | Engine 未启动；许可未接受；不启动服务 |
| PostgreSQL pull、pg_isready、Windows SELECT 1 | NOT_RUN | CR 已批准，但本会话不拉取或启动服务；留给 Orchestrator 隔离候选门禁 |
| stop/restart volume retention | NOT_RUN | 服务未启动 |
| 隔离备份 / 新 project、新卷恢复 | NOT_RUN | 服务未启动；没有覆盖任何数据 |
| Docker Desktop daemon | WAITING_EXTERNAL | Orchestrator 报告 Engine 未启动；条款接受和 Engine 可用性仍待后续用户/协调器动作 |

## Qdrant 配置安全约束

PostgreSQL password 与 Qdrant API key 均使用必填的本地环境变量，未在 repo 创建 .env、.kw/secrets 或任何凭据文件。项目专属 Compose bridge network 只接入 PostgreSQL 与 Qdrant。单节点配置明确 cluster.enabled=false、service.grpc_port=null。用户端只有 127.0.0.1:55432 → PostgreSQL 5432 与 127.0.0.1:56333 → Qdrant REST 6333 映射。命名卷 pg_data、qdrant_data 与 qdrant_snapshots 供后续持久化/备份验证。禁止 down -v、volume prune 或操作不相关卷。

## 安装审批记录

初次 auto-review 拒绝了“下载官方 Docker Desktop 并以 --user 安装”的命令，理由是仓库 AGENTS.md 写明 Phase 0 不安装软件。第二次附上 P1 用户授权后仍被拒，reviewer 说明未识别到可信用户授权。用户随后直接回复“允许安装”。较早的执行被协调器接手；后续本地安装尝试有官方文件校验记录，但 install_exit 为空，紧随其后的检查因 LOCALAPPDATA 重定向而未定位实际安装。Orchestrator 随后确认 per-user Desktop 与 CLI/Compose 版本均已安装，Engine 未启动；许可未接受。早期 Windows 功能报告称当时没有改动，之后用户已授权管理员 WSL 启用尝试，但结果未知、功能状态未确认且未重启；需本机管理员确认。详见 verification/environment/docker/docker-install-review.json 与 docker-desktop-install.json。

## 本地依据与官方安全资料

- Docker Windows installation & permission requirements：https://docs.docker.com/desktop/setup/install/windows-install/
- Docker Desktop 4.99.0 release notes and checksum link：https://docs.docker.com/desktop/release-notes/
- PostgreSQL 16.16 release：https://www.postgresql.org/about/news/postgresql-187-1712-1616-1520-1425-and-19-rc-2-released-3442/
- Qdrant v1.17.1 release：https://github.com/qdrant/qdrant/releases/tag/v1.17.1
- Qdrant current v1.20.2 release：https://github.com/qdrant/qdrant/releases/tag/v1.20.2
- Qdrant single-node/internal gRPC advisory：https://github.com/qdrant/qdrant/security/advisories/GHSA-frc7-44gm-99qq
- Qdrant API key/JWT internal gRPC advisory：https://github.com/qdrant/qdrant/security/advisories/GHSA-3gph-6c29-p29v
- Qdrant logger file-write advisory：https://github.com/qdrant/qdrant/security/advisories/GHSA-f632-vm87-2m2f
- Qdrant service configuration：https://qdrant.tech/documentation/operations/configuration/
- Qdrant /readyz monitoring endpoint：https://qdrant.tech/documentation/operations/monitoring/

## Git 与交付边界

所有项目变更限于任务 allowlist；未运行 B00–B08，未改冻结规划、账本、根配置、Python lock 或业务代码。请求由协调器核验后决定是否接受 READY_FOR_REVIEW。本交付不声称 Docker、PostgreSQL、Qdrant、P1 或项目已完成。
