# 冻结契约变更申请

- CR ID/node/plan_version：CR-P1-POSTGRES-16.16 / P1.docker_services / KW-P0-2026-11-18
- plan_hash：6ef838b43dc6a6a8d1680c8b9503e55fe7a63c6ca9c4238e4942acf8d495d1df
- 状态：DRAFT — 等待技术负责人批准；本文件不是批准记录。

## 冲突位置、最小复现及影响

docs/planning/TECH_STACK.md 和 templates/compose.reference.yaml 冻结 PostgreSQL 候选为 postgres:16.13-bookworm。PostgreSQL Global Development Group 于 2026-11-12 发布 PostgreSQL 16.16；官方公告说明该次版本修复 52 项安全漏洞和 140 多项缺陷。继续拉取或运行 16.13 会落后于当前同一主版本的安全更新，因此 P1 明确禁止运行冻结候选，直到负责人决定。

官方发布说明确认 16.16 是 2026-11-12 的 PostgreSQL 16 更新版本，且 PostgreSQL 16 仍在支持期内。Docker Hub 官方镜像页显示 postgres:16.16-bookworm 可用。对本主机 AMD64 容器平台，Docker Hub 当前列出的 Linux/amd64 平台 manifest digest 为 sha256:f45d631d050c2fe068f384ed6c4faac506e9cd7f4da605ce703648e4b0985848；多平台 index digest 为 sha256:a49f7438ba03589f9b1ca062f2da82e899b48ea1dc50d2449fa09a70be83ebca。这里仅记录 registry 页面证据，尚未本地拉取或运行镜像。

## 建议修改与替代、范围、迁移/兼容/测试

建议仅将 P1 Docker 基线从 postgres:16.13-bookworm 修订为 postgres:16.16-bookworm，并固定上述 Linux/amd64 平台 manifest digest。不要更改 PostgreSQL 主版本、数据库接口、数据模型、依赖图或任何 Batch。

官方 16.16 release notes 表明同一 16.X 系列更新不要求 dump/restore，但提示应评估变更记录中涉及的 REINDEX 情况。当前环境没有已验证的 PostgreSQL 数据卷；在 CR 批准后仍须以新建的 P1 卷实测初始化、pg_isready、Windows psycopg SELECT 1、停止/重启持久化和隔离备份恢复。不得把该兼容性说明替代真实测试。

风险包括更新版本运行行为变化，以及在 tag 对应的 registry 内容改变时误用非预期镜像。通过固定 digest、本地 pull 后核对镜像 ID/平台、记录 PostgreSQL server_version 与实际 health/SQL/持久化日志来控制风险。审批前不拉取也不运行 16.16 或 16.13。

官方来源：

- [PostgreSQL 16.16 安全公告，2026-11-12](https://www.postgresql.org/about/news/postgresql-187-1712-1616-1520-1425-and-19-rc-2-released-3442/)
- [PostgreSQL 16.16 release notes](https://www.postgresql.org/docs/release/16.16/)
- [PostgreSQL 支持版本政策](https://www.postgresql.org/support/versioning/)
- [Docker Hub postgres:16.16-bookworm Linux/amd64 manifest](https://hub.docker.com/layers/library/postgres/16.16-bookworm/images/sha256-f45d631d050c2fe068f384ed6c4faac506e9cd7f4da605ce703648e4b0985848)

## 哪些独立工作可继续

Docker Desktop 用户级安装、主机盘点、Qdrant 单节点安全核对、Qdrant-only Compose 与 Windows 检查脚本、镜像 registry 证据、配置静态审阅均可继续。PostgreSQL Compose service、pull、container、SQL、双服务健康、PG 持久化与恢复测试必须等批准后进行。

## 需要技术负责人决定

1. 是否批准 P1 PostgreSQL 镜像候选变更为 postgres:16.16-bookworm。
2. 是否批准固定 Linux/amd64 digest sha256:f45d631d050c2fe068f384ed6c4faac506e9cd7f4da605ce703648e4b0985848（多平台 index sha256:a49f7438ba03589f9b1ca062f2da82e899b48ea1dc50d2449fa09a70be83ebca）。
3. 如批准，指定唯一实施者与新 plan_version、生效范围、审查人和时间，并版本化冻结计划及其 manifest。

## 决定

未批准。等待技术负责人裁决。当前工作会话无权批准本 CR 或修改冻结规划。
