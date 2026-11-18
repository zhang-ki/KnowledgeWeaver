# P1 Docker services delivery

- task_id/batch_id/attempt_id/instance_id/plan_version/plan_hash：P1.docker_services / P1 / delegated attempt not supplied / 887b0733-5d66-4cfc-b56d-dec7555daeaa / KW-P0-2026-11-18 / 6ef838b43dc6a6a8d1680c8b9503e55fe7a63c6ca9c4238e4942acf8d495d1df
- 实际 worktree/branch/base_commit/head_commit：assigned Codex worktree / task/p1-docker-services / c8d2744188d089976a0be532da72fbf40b0b9c25 / 329aa3ec97f1ed58012991ed5e7caf5453b922c7 (offline delivery commit; this supplemental Orchestrator report follows in the next task-branch commit).
- 目标与实现清单：完成主机和官方镜像来源核验；准备受安全约束的 Qdrant-only Compose、配置、Windows 验证脚本；起草 PostgreSQL 16.16 CR。Orchestrator 已报告 Docker Desktop 与 CLI/Compose 二进制版本；Engine 未启动，服务实测及双服务 Compose 尚未完成。
- 逐文件 allowlist 映射：
  - infra/compose.yaml — Qdrant only; PostgreSQL intentionally omitted pending CR.
  - environment.lock.json — registry-observed Qdrant v1.17.1 amd64 digest; explicitly marks local pull/runtime NOT_RUN.
  - verification/environment/docker/host-inventory.json — host read-only inventory and failed checks.
  - verification/environment/docker/docker-install-review.json — auto-review rejections and coordinator handover.
  - verification/environment/docker/docker-desktop-install.json — local installer checksum/signature evidence and immediate installation observation.
  - verification/environment/docker/offline-validation.log — sanitized local validation output and SHA-256 evidence.
  - verification/environment/docker/qdrant.production.yaml — disables gRPC and cluster mode.
  - verification/environment/docker/verify_qdrant.ps1 — readiness and isolated collection write/query/delete; secret-safe output.
  - verification/environment/docker/verify_postgres.ps1 — Windows psycopg SELECT 1; connection string is read from environment and never printed.
  - docs/changes/CR-P1-POSTGRES-16.16.md — draft only; awaits technical lead approval.
  - docs/implementation/P1-docker.md — implementation and blockers record.
  - docs/deliveries/P1-docker.md — this delivery.
- 公共接口/配置指纹与依赖交付：Compose port mapping 127.0.0.1:56333 → REST 6333; named volumes qdrant_data and qdrant_snapshots; Qdrant platform manifest sha256:cd3e42737c684ee516ae5533218be93fd5288f41d0a466ed18dbdc22ef52a000. PostgreSQL tag/digest not locked pending CR. No Python dependency changes.

## 验证记录

| 命令 / 检查 | 结果 / 退出码 | 时间（本地 / UTC） | 证据 |
|---|---|---|---|
| git rev-parse --show-toplevel; git branch --show-current; git rev-parse HEAD; git status --short; git worktree list --porcelain | PASS; branch task/p1-docker-services, HEAD equals base SHA, clean at start | 2026-11-18 20:43 / 12:43Z | host-inventory.json |
| Windows registry / architecture / local disk inventory | PARTIAL; build 26200 / display 25H2 / ProductName says Windows 10 Home China; AMD64; C 69.3 GB and D 202.3 GB free | 2026-11-18 20:46:50 +08 / 12:46:50Z | host-inventory.json |
| Get-CimInstance Win32_OperatingSystem, Win32_Processor, Win32_LogicalDisk | FAIL; CIM resources inaccessible | 2026-11-18 20:46 +08 / 12:46Z | host-inventory.json |
| systeminfo.exe | FAIL; exit 1 | 2026-11-18 20:46 +08 / 12:46Z | host-inventory.json |
| wsl.exe --version / --list / --status | FAIL; exit 1 / 1 / 50; no setup changes | 2026-11-18 20:46 +08 / 12:46Z | host-inventory.json |
| Get-WindowsOptionalFeature -Online | BLOCKED; requires elevation | 2026-11-18 20:46 +08 / 12:46Z | host-inventory.json |
| docker.exe detection and Desktop/service presence | NOT_FOUND in initial read-only inventory | 2026-11-18 20:46 +08 / 12:46Z | host-inventory.json |
| Docker Desktop installation and versions | PASS per Orchestrator post-attempt check: Desktop 4.99.0.245822, Authenticode Valid, Docker 29.9.3 (exit 0), Compose v5.7.0 (exit 0); no Docker processes, Engine not started, license not accepted, VMP unverified; original install_exit remains null | 2026-11-18T21:29:14.4453889+08:00 | docker-desktop-install.json |
| Docker Hub Qdrant v1.17.1 manifest lookup | PASS as registry-page evidence only; local pull not run | 2026-11-18; web source observation | environment.lock.json |
| PostgreSQL 16.16 official release/tag/digest lookup | PASS as official source evidence; no pull/run; approval pending | 2026-11-18; web source observation | CR-P1-POSTGRES-16.16.md |
| JSON parse, PowerShell AST parse, Compose source safety guard, credential/path/whitespace scan, git diff --check | PASS; no secrets or user paths found; git diff --check exit 0 | 2026-11-18 21:15:52 +08 / 13:15:52Z | offline-validation.log, SHA-256 98feaf5666d4c38ee45ba4e48fb62ce5d2126b125cdfcd842766cb0293b882d7 |
| Qdrant verifier missing-key branch | NOT_RUN by design; script exited 2 and emitted a redacted missing-config result | 2026-11-18 21:10:43 +08 / 13:10:43Z | offline-validation.log |
| PostgreSQL verifier missing-DSN branch | NOT_RUN by design; script exited 2 and emitted a redacted missing-config result | 2026-11-18 21:10:36 +08 / 13:10:36Z | offline-validation.log |
| Docker CLI/Compose version and config parse | Version check PASS per Orchestrator report; static `compose -f infra/compose.yaml config --quiet` PASS, exit 0, using a temporary non-secret validation value that was removed; Engine and containers were not started, so this is not Qdrant runtime validation | 2026-11-18T21:34:47.7104441+08:00 | docker-desktop-install.json, Orchestrator report |
| WSL / Windows feature state | WAITING_LOCAL_ADMIN_CONFIRMATION; user authorization was provided; Orchestrator's administrator WSL no-distribution enable script exited 0, but Wsl/E_ACCESSDENIED appeared; Get-WindowsOptionalFeature still requires elevation, RunAs read-only recheck was rejected; feature state unconfirmed, no restart | recorded 2026-11-18T13:41:31Z; attempt observation time not supplied | docker-install-review.json |
| Qdrant readiness/CRUD, PG health/SELECT 1, restart retention, isolated restore | NOT_RUN / blocked as described above | — | — |

No service logs, local image signatures, container digests, SQL result, persistence result, or restore result are claimed. No credentials were created or stored. Registry page evidence is distinguished from an image pulled and inspected locally.

- 真实服务/provider 与 Fake 区分：无服务启动；Qdrant registry metadata only; no fake service.
- 验收逐项证据、known limitations、blocked事项：见 docs/implementation/P1-docker.md；主要等待用户审阅并决定是否接受 Docker Subscription Service Agreement、本机管理员确认 WSL/VMP/BIOS 前置状态、技术负责人批准 CR、Engine/service gates 及隔离恢复。WSL 启用已有用户授权，当前阻塞是本机权限与状态未确认。
- Artifact 路径/hash：offline-validation.log 为 98feaf5666d4c38ee45ba4e48fb62ce5d2126b125cdfcd842766cb0293b882d7；其他非自引用 artifact hashes 列在下方。

| Artifact | SHA-256 |
|---|---|
| infra/compose.yaml | 006e89673858edaf59ae3bd708e5a643daf1b405ebc429e30f01c1c6a6414ad1 |
| environment.lock.json | af007f7f3dd76e5d5f1a50c21f6ecb2837b4a6917c455b719f0ef3ca7c199f1d |
| verification/environment/docker/host-inventory.json | 10bac5ff7e4b371ba5ed760a65749dbfa100bad2c52e993ad3c0e28792b4e198 |
| verification/environment/docker/docker-install-review.json | 7401b822ca6dd07a9139702a5d98eab9fa320f1c367b343573876f6fde8512f2 |
| verification/environment/docker/docker-desktop-install.json | e6cb2495ee494992839c84f7eca438b3c557d75eb644b6631c1abc31ded5d925 |
| verification/environment/docker/qdrant.production.yaml | 05c64006d8d0d0f96f305d50184f6e758d8fc5540919b9b74e2e0486322a9eb9 |
| verification/environment/docker/verify_qdrant.ps1 | 71c455d950dbe208915b122eece9fe23926e342d0693c400a9548d24943b7be5 |
| verification/environment/docker/verify_postgres.ps1 | 6a8685870c48afccff0a1b3208cf095ff0e4f4e8e9a0ab92dae5460b87c41bf2 |
| verification/environment/docker/offline-validation.log | 98feaf5666d4c38ee45ba4e48fb62ce5d2126b125cdfcd842766cb0293b882d7 |
| docs/changes/CR-P1-POSTGRES-16.16.md | b5adc785e59aad7ec3f501d8e375969e5762ab86242b8c7b21dfc9972544c61e |
| docs/implementation/P1-docker.md | 7fad8b2c2ad72df64955203c261b480973cba236e04ec8bb2bc413e8fe121004 |
- 后继消费步骤：用户决定许可且本机管理员确认系统前置状态后再执行 Docker Engine/service gates；技术负责人批准 PostgreSQL CR 后补齐双服务 Compose 与真实 service gates。
- 申请 READY_FOR_REVIEW（partial evidence, blockers retained）；不自行写 INTEGRATED/COMPLETED。
