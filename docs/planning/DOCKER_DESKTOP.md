# Docker Desktop 与本机基础设施

P1在安装授权后检查Windows11支持条件、虚拟化、Docker Desktop许可与磁盘。WSL2仅可作为Docker内部引擎；不要求安装Ubuntu发行版、不把代码移入WSL、不自动改防火墙或安全设置。安装步骤以官方页面为准：[Windows安装](https://docs.docker.com/desktop/setup/install/windows-install/)。

配置参考是 templates/compose.reference.yaml，当前只是审查模板，尚未执行或验证。P1验证后生成infra/compose.yaml与environment.lock.json；精确镜像tag和验证digest固定，不用latest。业务应用保持Windows运行。

| 服务 | 主机地址 | 容器地址 | 数据 |
|---|---|---|---|
| PostgreSQL | 127.0.0.1:55432 | postgres:5432 | Compose命名卷pg_data → /var/lib/postgresql/data |
| Qdrant REST | 127.0.0.1:56333 | qdrant:6333 | qdrant_data → /qdrant/storage |

Docker命名卷由Desktop管理，避免数据库目录直接绑NTFS导致权限/性能差异；原始资料/索引在Windows data/。备份导出backups/。内部网络只有两个服务，不暴露gRPC/管理公网端口。Windows Python连接127.0.0.1映射端口，不能把Compose服务名postgres作为主机DNS。

Postgres容器healthcheck使用镜像内pg_isready，随后Windows用psycopg真实SELECT1判读写权限。Qdrant就绪由Windows Invoke-RestMethod带api-key请求 /readyz，再做隔离collection写入/查询/删除；不假设官方镜像自带curl/wget。Compose的running不等于Qdrant ready，统一健康脚本须轮询≤60s并分别报告。Qdrant健康接口依据：[监控文档](https://qdrant.tech/documentation/operations/monitoring/)。

下列命令仅供P1授权后执行：
```powershell
docker compose --env-file .env -f infra/compose.yaml -p knowledgeweaver config --quiet
docker compose --env-file .env -f infra/compose.yaml -p knowledgeweaver up -d
docker compose -f infra/compose.yaml -p knowledgeweaver ps
docker compose -f infra/compose.yaml -p knowledgeweaver logs --tail 100
docker compose -f infra/compose.yaml -p knowledgeweaver stop
docker compose --env-file .env -f infra/compose.yaml -p knowledgeweaver up -d
```
stop保留数据，restart不能应用所有配置变化；配置改动用up重新创建。常规操作不得down -v或docker volume prune。端口冲突先报告/选择新端口并更新Windows配置，不停止其他软件。密码改变后已有PG卷不会因环境变量自动改密码，使用审查后的SQL迁移/新卷恢复流程。

备份一致性：暂停导入/GC，记录active generation和各source/index hash；PG用pg_dump -Fc写容器临时文件，再docker cp到Windows（避免PowerShell二进制重定向损坏）；Qdrant按collection创建snapshot、用Invoke-WebRequest -OutFile保存；本地索引manifest与许可原文同步复制。密钥不打包。备份记录文件hash、server versions、generation、时间。

恢复先到新Compose project与新卷/端口，pg_restore与Qdrant snapshot恢复后核对相同generation、chunk计数、引用、向量维度和查询；成功且明确批准后才切换本机配置。失败保留现服务，不能直接覆盖唯一数据副本。物理删除卷/备份属于不可逆操作，需授权。重启恢复验证包括Docker退出再启动后卷数据仍在；自动重启策略unless-stopped不替代应用状态恢复。
