# P1 Docker services delivery

- task_id/batch_id/attempt_id/instance_id/plan_version/plan_hash：P1.docker_services / P1 / p1-docker-services-attempt-001 / 887b0733-5d66-4cfc-b56d-dec7555daeaa / KW-P0-2026-11-18 / 6ef838b43dc6a6a8d1680c8b9503e55fe7a63c6ca9c4238e4942acf8d495d1df
- task_session：01a1eeb0-77c0-7982-a238-0ddbbcf5eedf
- 实际 worktree/branch/original_base/pre-change_HEAD：assigned Codex worktree / task/p1-docker-services / c8d2744188d089976a0be532da72fbf40b0b9c25 / b5ed7644f7966424d5b1780c928dff7b325bd730；本次候选 commit SHA 由 Git 交付结果记录。
- 目标与实现清单：完成主机和官方镜像来源核验；按已批准 CR 准备 PostgreSQL + Qdrant 双服务 Compose、锁定镜像清单和 Windows 验证脚本。PostgreSQL 16.16 CR 已批准；Docker Engine 未启动，镜像 pull、服务及双服务运行门禁仍未执行。
- 逐文件 allowlist 映射：
  - infra/compose.yaml — PostgreSQL 16.16 + Qdrant v1.17.1; loopback-only ports, project-local network, named volumes and required local secrets.
  - environment.lock.json — registry-observed amd64 manifest/index digests for both images; CR approval source recorded; local pull/runtime NOT_RUN.
  - verification/environment/docker/host-inventory.json — host read-only inventory and failed checks.
  - verification/environment/docker/docker-install-review.json — auto-review rejections and coordinator handover.
  - verification/environment/docker/docker-desktop-install.json — local installer checksum/signature evidence and immediate installation observation.
  - verification/environment/docker/offline-validation.log — sanitized local validation output and SHA-256 evidence.
  - verification/environment/docker/qdrant.production.yaml — disables gRPC and cluster mode.
  - verification/environment/docker/verify_qdrant.ps1 — readiness and isolated collection write/query/delete; secret-safe output.
  - verification/environment/docker/verify_postgres.ps1 — Windows psycopg SELECT 1; connection string is read from environment and never printed.
  - docs/changes/CR-P1-POSTGRES-16.16.md — approved by the user in the Orchestrator conversation; runtime event 29; scope limited to the patch image.
  - docs/implementation/P1-docker.md — implementation and blockers record.
  - docs/deliveries/P1-docker.md — this delivery.
- 公共接口/配置指纹与依赖交付：PostgreSQL 127.0.0.1:55432 → 5432，database knowledgeweaver/user kw，volume pg_data；Qdrant 127.0.0.1:56333 → REST 6333，volumes qdrant_data/qdrant_snapshots。两者为 linux/amd64、各自固定 platform manifest digest，仅接入 project-local services network。No Python dependency changes.

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
| PostgreSQL 16.16 CR decision | APPROVED by user in Orchestrator conversation; runtime event 29; scope is only postgres:16.16-bookworm linux/amd64 manifest digest, architecture and PG major unchanged | timestamp not supplied in handoff | CR-P1-POSTGRES-16.16.md |
| PostgreSQL registry tag/digest evidence | PASS as registry-page evidence only; approved platform manifest sha256:f45d631d050c2fe068f384ed6c4faac506e9cd7f4da605ce703648e4b0985848; index sha256:a49f7438ba03589f9b1ca062f2da82e899b48ea1dc50d2449fa09a70be83ebca; local pull/runtime NOT_RUN | 2026-11-18; source evidence recorded in CR | environment.lock.json, CR-P1-POSTGRES-16.16.md |
| JSON parse, PowerShell AST parse, Compose source safety guard, credential/path/whitespace scan, git diff --check | PASS; no secrets or user paths found; git diff --check exit 0 | 2026-11-18 21:15:52 +08 / 13:15:52Z | offline-validation.log, SHA-256 98feaf5666d4c38ee45ba4e48fb62ce5d2126b125cdfcd842766cb0293b882d7 |
| Current environment.lock.json JSON parse and dual-service source guard | PASS; exactly postgres/qdrant, required digests, platform, loopback mappings, required env vars, volumes, healthcheck, network and Qdrant gRPC/cluster constraints asserted | 2026-11-18 23:01:29 +08 / 15:01:29Z | environment.lock.json, infra/compose.yaml, qdrant.production.yaml |
| Current changed-file allowlist, secret/path scan, trailing whitespace and git diff --check | PASS; five changed files all in this task allowlist; scan reported no filenames | 2026-11-18 23:01:29 +08 / 15:01:29Z | this task validation |
| PowerShell AST parse | N/A; verify_postgres.ps1 and verify_qdrant.ps1 unchanged in this task | 2026-11-18 23:01:29 +08 / 15:01:29Z | — |
| Qdrant verifier missing-key branch | NOT_RUN by design; script exited 2 and emitted a redacted missing-config result | 2026-11-18 21:10:43 +08 / 13:10:43Z | offline-validation.log |
| PostgreSQL verifier missing-DSN branch | NOT_RUN by design; script exited 2 and emitted a redacted missing-config result | 2026-11-18 21:10:36 +08 / 13:10:36Z | offline-validation.log |
| Earlier Qdrant-only Compose static parse | PASS per Orchestrator; exit 0; this was the prior Qdrant-only candidate and is not a parse result for the current dual-service file | 2026-11-18T21:34:47.7104441+08:00 | prior Orchestrator report |
| Current dual-service Compose static parse | NOT_RUN by this Work session; Orchestrator will parse the isolated candidate with `docker compose --env-file <local-ephemeral-env-file> -f infra/compose.yaml -p knowledgeweaver config --quiet`; no daemon/container action requested here | pending Orchestrator candidate check | infra/compose.yaml |
| WSL / Windows feature state | WAITING_LOCAL_ADMIN_CONFIRMATION; user authorization was provided; Orchestrator's administrator WSL no-distribution enable script exited 0, but Wsl/E_ACCESSDENIED appeared; Get-WindowsOptionalFeature still requires elevation, RunAs read-only recheck was rejected; feature state unconfirmed, no restart | recorded 2026-11-18T13:41:31Z; attempt observation time not supplied | docker-install-review.json |
| PostgreSQL pull/pg_isready/Windows SELECT 1; Qdrant readiness/CRUD; restart retention; isolated restore | NOT_RUN; CR is approved but all runtime gates remain for Orchestrator's isolated candidate | — | — |

No service logs, local image signatures, container digests, SQL result, persistence result, or restore result are claimed. No credentials were created or stored. Registry page evidence is distinguished from an image pulled and inspected locally.

- 真实服务/provider 与 Fake 区分：无服务启动；Qdrant registry metadata only; no fake service.
- 验收逐项证据、known limitations、blocked事项：见 docs/implementation/P1-docker.md；CR 已批准，但当前双服务 Compose 解析与真实服务门禁待 Orchestrator 隔离候选核验。另需用户审阅 Docker Subscription Service Agreement、本机管理员确认 WSL/VMP/BIOS 前置状态，并完成 Engine/service gates 及隔离恢复。
- Artifact 路径/hash：offline-validation.log 为 98feaf5666d4c38ee45ba4e48fb62ce5d2126b125cdfcd842766cb0293b882d7；其他非自引用 artifact hashes 列在下方。

| Artifact | SHA-256 |
|---|---|
| infra/compose.yaml | 69d90a784dcfe0ebfdfc43d05070eea64b161b9f6f48d74d7bf1fd54b2f9a4d5 |
| environment.lock.json | eb3eeece8d9c31e2ebb583f28e6b3bc7008bcb9498cc5a1c1def79a8cff17444 |
| verification/environment/docker/host-inventory.json | 10bac5ff7e4b371ba5ed760a65749dbfa100bad2c52e993ad3c0e28792b4e198 |
| verification/environment/docker/docker-install-review.json | 7401b822ca6dd07a9139702a5d98eab9fa320f1c367b343573876f6fde8512f2 |
| verification/environment/docker/docker-desktop-install.json | e6cb2495ee494992839c84f7eca438b3c557d75eb644b6631c1abc31ded5d925 |
| verification/environment/docker/qdrant.production.yaml | 05c64006d8d0d0f96f305d50184f6e758d8fc5540919b9b74e2e0486322a9eb9 |
| verification/environment/docker/verify_qdrant.ps1 | 71c455d950dbe208915b122eece9fe23926e342d0693c400a9548d24943b7be5 |
| verification/environment/docker/verify_postgres.ps1 | 6a8685870c48afccff0a1b3208cf095ff0e4f4e8e9a0ab92dae5460b87c41bf2 |
| verification/environment/docker/offline-validation.log | 98feaf5666d4c38ee45ba4e48fb62ce5d2126b125cdfcd842766cb0293b882d7 |
| docs/changes/CR-P1-POSTGRES-16.16.md | 4b53c77822f10e5dfb13132c37d7c3465b0acc11c82694678fbc56678e89ee0f |
| docs/implementation/P1-docker.md | d091ec5a93e464a970b495c9c0ad24e941d11e328a8b57bc5067d9bf774a3614 |
- 后继消费步骤：Orchestrator 解析本次双服务 Compose 隔离候选并完成授权后的 Docker Engine/service gates；用户决定许可且本机管理员确认系统前置状态后，再执行运行验证。CR-P1-POSTGRES-16.16 已批准。
- 申请 READY_FOR_REVIEW（partial evidence, blockers retained）；不自行写 INTEGRATED/COMPLETED。
