# YiQiao 运维指南

**简体中文** | [English](OPERATIONS.md)

本手册适用于受支持的单主机 Docker Compose 部署。除非章节另有说明，否则所有命令
均从仓库根目录运行。

## 环境要求

- Docker 支持的 64 位 Linux、macOS 或 Windows 主机。
- Docker Desktop，或安装了 Docker Compose v2 插件的 Docker Engine。
- 使用源码安装和升级时需要 Git。
- 备份和恢复流程需要 Bash。Windows 用户应在已启用容器引擎集成的 WSL 2 中运行
  这些维护章节。
- 能够通过 HTTPS 访问容器镜像仓库和所有远程模型服务商。
- 回环地址上的 `3000` 和 `8888` 端口可用，或已在 `server/.env` 中设置替代值。
- 存储空间足以容纳 PostgreSQL 向量、Neo4j 图数据、请求日志、导出结果和保留的
  导入文件。

初始化前确认工具可用：

```bash
docker version
docker compose version
```

## 初始化

Linux 和 macOS：

```bash
./scripts/init.sh
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1
```

脚本会在需要时将 `server/.env.example` 复制为 `server/.env`，生成
`POSTGRES_PASSWORD`、`NEO4J_PASSWORD` 和 `JWT_SECRET`，创建
`server/history`，并验证 Compose 配置。脚本会保留非空密钥，不会替换已经存在的环境文件。

请将 `server/.env` 视为密钥。不要提交该文件、将其附在 Issue 中，或把未加密副本
与数据库备份存放在一起。

## 启动与停止

默认模式会拉取已发布的 API 和控制台镜像：

```bash
cd server
docker compose up -d
docker compose ps
```

从当前检出的源码构建应用镜像：

```bash
cd server
docker compose \
  -f docker-compose.yaml \
  -f docker-compose.build.yaml \
  up -d --build
```

应用生产加固覆盖配置，以强制启用身份验证、移除应用容器能力并限制 Docker 日志
轮转：

```bash
cd server
docker compose \
  -f docker-compose.yaml \
  -f docker-compose.production.yaml \
  up -d
```

PowerShell 可在一行中使用相同的 `docker compose` 参数。

停止容器但保留数据：

```bash
cd server
docker compose stop
```

删除容器和网络但保留数据：

```bash
cd server
docker compose down
```

## 端点与网络暴露

| 设置 | 默认值 | 用途 |
| --- | --- | --- |
| `API_BIND_ADDRESS` | `127.0.0.1` | API 的主机监听接口 |
| `API_PORT` | `8888` | API 的主机端口 |
| `DASHBOARD_BIND_ADDRESS` | `127.0.0.1` | 控制台的主机监听接口 |
| `DASHBOARD_PORT` | `3000` | 控制台的主机端口 |
| `PUBLIC_API_URL` | 根据 `API_PORT` 推导 | 反向代理后的浏览器可见 API URL |
| `PUBLIC_DASHBOARD_URL` | 根据 `DASHBOARD_PORT` 推导 | 控制台公共来源与身份验证 URL |

PostgreSQL 和 Neo4j 不映射主机端口，只连接内部后端网络。控制台和 API 默认监听
回环地址。

远程访问应优先使用 TLS 反向代理或私有网络。将公共 URL 设置为外部可见的 HTTPS
地址，将绑定地址限制在代理拓扑允许的最小范围内，然后重启应用容器。不要直接暴露
数据库服务。


## 应用镜像

| 设置 | 默认值 |
| --- | --- |
| `YIQIAO_API_IMAGE` | `ghcr.io/daizhongming/yiqiao-api:latest` |
| `YIQIAO_DASHBOARD_IMAGE` | `ghcr.io/daizhongming/yiqiao-dashboard:latest` |
| `YIQIAO_PULL_POLICY` | `always` |

为了让生产部署可复现，请将 `latest` 替换为已审查的标签或不可变摘要。升级前解析
配置中的应用镜像引用，并记录两个仓库摘要：

```bash
cd server
set -- $(docker compose config --images yiqiao yiqiao-dashboard)
if [ "$#" -ne 2 ]; then
  echo "Expected two resolved application images" >&2
  exit 1
fi
printf 'Resolved image: %s\n' "$@"
docker image inspect --format '{{index .RepoDigests 0}}' "$@"
```

## 首次设置与模型服务商

所有服务健康后，打开 <http://localhost:3000/setup>。向导会：

1. 创建第一个管理员。
2. 选择并配置内置的 LLM 和嵌入服务商。
3. 创建第一个项目 API 密钥。
4. 记录预期使用场景。
5. 执行一次记忆写入。

服务商列表来自正在运行的 API 镜像。配置页面也可以设置自定义 OpenAI 兼容基础
URL 和模型标识。处理生产数据前，请先使用页面中的连接测试。

通过控制台保存的服务商 API 密钥会作为运行时配置存入应用数据库。配置 API 的响应
会隐藏这些值，但这不能替代外部密钥管理器。应相应保护 PostgreSQL 卷、逻辑转储、
管理员会话和控制台访问。要求更严格的部署可以通过环境注入服务商凭据，并限制配置
权限。

容器启动不依赖服务商凭据。在配置的路由可用前，记忆提取、嵌入、重排和导入操作
都会失败。

## 持久化

| 位置 | 内容 | 生命周期 |
| --- | --- | --- |
| Compose 卷 `postgres_db` | 用户、API 密钥、设置、请求、导出和向量数据 | `docker compose down` 后保留；`down -v` 时删除 |
| Compose 卷 `neo4j_data` | 图实体和关系 | `docker compose down` 后保留；`down -v` 时删除 |
| `server/history/` | 记忆历史 SQLite 数据库、导入工作区、保留的源文件和本地运行时文件 | 主机目录；单独备份和清理 |
| `server/.env` | 部署设置和密钥 | 主机文件；安全保存且不得提交 |

导出任务记录与结果保存在应用 PostgreSQL 数据库中。从控制台下载的文件只存在于
浏览器或 API 客户端的保存位置，需要单独制定客户端保留策略。

将 `PROJECT` 设置为当前部署实际使用的 Compose 项目。仓库默认值是 `yiqiao-v3`；
显式的 `-p` 参数会覆盖它。每条备份命令都必须使用相同的值，并通过项目标签发现
实际卷名：

```bash
cd server
PROJECT=yiqiao-v3
docker compose -p "$PROJECT" config --volumes
docker volume ls --filter "label=com.docker.compose.project=$PROJECT"
```

## 备份

每次升级前都要备份，并在另一台主机上测试恢复。完整备份包括两个 PostgreSQL
数据库、Neo4j 卷、`server/history` 和一份加密的环境设置副本。应用数据库转储包含
服务端导出任务记录与结果；用户下载到客户端设备的文件需要另外保留。

请安排维护窗口，确保 PostgreSQL、Neo4j 和 history 处于同一个静止应用状态。如果
环境覆盖了默认值，请替换数据库名和用户名。全新的 Compose 卷只创建
`yiqiao_app`；高级 `APP_DB_NAME` 覆盖所指定的数据库必须已存在于目标服务器。

```bash
set -euo pipefail
mkdir -p backups
cd server
PROJECT=yiqiao-v3
docker compose -p "$PROJECT" stop yiqiao-dashboard yiqiao neo4j
docker compose -p "$PROJECT" exec -T postgres \
  pg_dump -U postgres -d postgres --format=custom \
  --file=/tmp/yiqiao-memory.dump
docker compose -p "$PROJECT" exec -T postgres \
  pg_dump -U postgres -d yiqiao_app --format=custom \
  --file=/tmp/yiqiao-application.dump
docker compose -p "$PROJECT" cp postgres:/tmp/yiqiao-memory.dump ../backups/yiqiao-memory.dump
docker compose -p "$PROJECT" cp postgres:/tmp/yiqiao-application.dump ../backups/yiqiao-application.dump
docker compose -p "$PROJECT" exec -T postgres \
  rm -f /tmp/yiqiao-memory.dump /tmp/yiqiao-application.dump
docker volume ls \
  --filter "label=com.docker.compose.project=$PROJECT" \
  --filter label=com.docker.compose.volume=neo4j_data
```

Neo4j Community 不提供 Enterprise 在线备份流程。在应用和 Neo4j 仍处于停止状态时，
使用主机或存储服务商的快照工具为列出的 `neo4j_data` 卷创建快照。随后在导入和
所有其他应用写入仍停止的情况下归档 history：

```bash
cd server
tar --create --gzip --file ../backups/yiqiao-history.tar.gz history
```

将 `server/.env` 单独保存在加密的密钥备份中。绝不要发布环境文件、数据库转储、
图快照、history 归档或下载的导出文件。只有在确认每个产物都存在且非空后才能重启：

```bash
cd server
PROJECT=yiqiao-v3
docker compose -p "$PROJECT" up -d
```

<a id="restore"></a>

## 恢复

请恢复到使用全新空卷的隔离替代部署中。在验证成功前，保持原部署和卷不变。初始化
`server/.env`，但在数据恢复完成前不要启动 API、控制台或 Neo4j。请使用物理上独立
的检出目录，确保其 `server/history` 绑定挂载和 `.env` 不会与当前部署重叠。以下
命令必须在 Bash 中运行；Windows 用户必须使用“环境要求”中说明的 WSL 2 环境。

只启动替代部署的 PostgreSQL 服务。首次运行初始化器会创建空的默认数据库，但不会
运行 YiQiao 迁移。在任何 API 进程能够连接前，复制并恢复两个自定义格式转储。将
`ACTIVE_PROJECT` 设置为备份使用的实际在线项目，并将示例中的
`RESTORE_PROJECT` 日期名称替换为唯一的小写名称。

运行代码块前，将加密密钥备份中的必要值选择性合并到替代部署的 `.env`。保留新生成
的 `POSTGRES_PASSWORD`，因为该凭据属于替代 PostgreSQL 集群。恢复 Neo4j 快照
所需的源 `NEO4J_USERNAME` 和 `NEO4J_PASSWORD`，以及源 `JWT_SECRET` 和所有必需的服务商密钥。不要整体复制旧 `.env`，
也不要替换恢复检出目录的绑定地址和路径。
必须在 `create neo4j` 捕获环境之前完成合并。并行恢复时，将 `ACTIVE_SERVER_DIR`
设置为在线检出目录。只有在另一台主机上恢复且当前检出目录和卷不可能存在时，才可将
其设置为空字符串；所有恢复目标的空状态和身份检查仍然必须执行。

维护经审查的 `PROTECTED_SOURCE_VOLUMES` 文件，每行写入一个保留的源卷名。原始源卷
和绑定挂载的数据库目录是只读证据：不要启动它们、将其连接到替代服务，也不要将其
列入任何删除清单。请单独记录绑定挂载源路径，并在审查渲染后的 Compose 配置时应用
相同的排除规则。

```bash
set -euo pipefail
ACTIVE_SERVER_DIR=/srv/yiqiao-live/server
RESTORE_SERVER_DIR="$(cd /srv/yiqiao-restore/server && pwd -P)"
BACKUP_DIR="$(cd /secure/path/to/yiqiao-backups && pwd -P)"
PROTECTED_SOURCE_VOLUMES=/secure/path/to/protected-source-volumes.txt
test -f "$PROTECTED_SOURCE_VOLUMES"
if test -n "$ACTIVE_SERVER_DIR"; then
  ACTIVE_SERVER_DIR="$(cd "$ACTIVE_SERVER_DIR" && pwd -P)"
  test "$RESTORE_SERVER_DIR" != "$ACTIVE_SERVER_DIR"
fi
cd "$RESTORE_SERVER_DIR"
test "$(pwd -P)" = "$RESTORE_SERVER_DIR"
test -f .env
test -d history
test -z "$(find history -mindepth 1 -print -quit)"
test -s "$BACKUP_DIR/yiqiao-memory.dump"
test -s "$BACKUP_DIR/yiqiao-application.dump"
test -s "$BACKUP_DIR/yiqiao-history.tar.gz"
ACTIVE_PROJECT=yiqiao-v3
RESTORE_PROJECT=yiqiao-restore-20260719
test -n "$RESTORE_PROJECT"
test "$RESTORE_PROJECT" != "$ACTIVE_PROJECT"
test -z "$(docker compose -p "$RESTORE_PROJECT" ps -aq)"
test -z "$(docker network ls -q \
  --filter "label=com.docker.compose.project=$RESTORE_PROJECT")"
for VOLUME in postgres_db neo4j_data; do
  if docker volume inspect "${RESTORE_PROJECT}_${VOLUME}" >/dev/null 2>&1; then
    echo "Refusing to reuse existing volume ${RESTORE_PROJECT}_${VOLUME}" >&2
    exit 1
  fi
done

docker compose -p "$RESTORE_PROJECT" up -d postgres
RESTORE_VOLUME="$(docker volume ls -q \
  --filter "label=com.docker.compose.project=$RESTORE_PROJECT" \
  --filter label=com.docker.compose.volume=postgres_db)"
test "$RESTORE_VOLUME" = "${RESTORE_PROJECT}_postgres_db"
test "$(docker volume inspect --format \
  '{{index .Labels "com.docker.compose.project"}}' "$RESTORE_VOLUME")" = \
  "$RESTORE_PROJECT"
test "$(docker volume inspect --format \
  '{{index .Labels "com.docker.compose.volume"}}' "$RESTORE_VOLUME")" = \
  postgres_db
if grep -Fqx -- "$RESTORE_VOLUME" "$PROTECTED_SOURCE_VOLUMES"; then
  echo "Refusing to use protected source volume $RESTORE_VOLUME" >&2
  exit 1
fi

POSTGRES_READY=false
for _ in {1..60}; do
  if docker compose -p "$RESTORE_PROJECT" exec -T postgres \
    pg_isready -q -h 127.0.0.1 -d postgres -U postgres; then
    POSTGRES_READY=true
    break
  fi
  sleep 2
done
test "$POSTGRES_READY" = true
test "$(docker compose -p "$RESTORE_PROJECT" exec -T postgres \
  psql -U postgres -d postgres -Atqc \
  "SELECT count(*) FROM pg_database WHERE datname IN ('postgres', 'yiqiao_app')")" = 2
for DATABASE in postgres yiqiao_app; do
  test "$(docker compose -p "$RESTORE_PROJECT" exec -T postgres \
    psql -U postgres -d "$DATABASE" -Atqc \
    "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema')")" = 0
done
```

**破坏性恢复边界：** 接下来的命令使用 `pg_restore --clean`。只有在上述数据库存在性、
表为空、项目标签和受保护源检查全部通过后，才能在唯一的替代项目中运行这些命令。
严禁将其用于非空目标、原地升级或选择性旧数据合并。这些工作流应返回
[迁移指南](MIGRATION.zh-CN.md)。

在同一个 shell 中继续：

```bash

docker compose -p "$RESTORE_PROJECT" cp "$BACKUP_DIR/yiqiao-memory.dump" postgres:/tmp/yiqiao-memory.dump
docker compose -p "$RESTORE_PROJECT" cp "$BACKUP_DIR/yiqiao-application.dump" postgres:/tmp/yiqiao-application.dump
# Destructive replacement restore. Never use this block as a merge.
docker compose -p "$RESTORE_PROJECT" exec -T postgres pg_restore \
  --exit-on-error --clean --if-exists --no-owner --no-privileges \
  --username postgres --dbname postgres /tmp/yiqiao-memory.dump
docker compose -p "$RESTORE_PROJECT" exec -T postgres pg_restore \
  --exit-on-error --clean --if-exists --no-owner --no-privileges \
  --username postgres --dbname yiqiao_app /tmp/yiqiao-application.dump
docker compose -p "$RESTORE_PROJECT" exec -T postgres \
  rm -f /tmp/yiqiao-memory.dump /tmp/yiqiao-application.dump

docker compose -p "$RESTORE_PROJECT" create neo4j
ACTIVE_NEO4J_VOLUME="$(docker volume ls -q \
  --filter "label=com.docker.compose.project=$ACTIVE_PROJECT" \
  --filter label=com.docker.compose.volume=neo4j_data)"
RESTORE_NEO4J_VOLUME="$(docker volume ls -q \
  --filter "label=com.docker.compose.project=$RESTORE_PROJECT" \
  --filter label=com.docker.compose.volume=neo4j_data)"
test "$RESTORE_NEO4J_VOLUME" = "${RESTORE_PROJECT}_neo4j_data"
test "$(docker volume inspect --format \
  '{{index .Labels "com.docker.compose.project"}}' "$RESTORE_NEO4J_VOLUME")" = \
  "$RESTORE_PROJECT"
test "$(docker volume inspect --format \
  '{{index .Labels "com.docker.compose.volume"}}' "$RESTORE_NEO4J_VOLUME")" = \
  neo4j_data
if grep -Fqx -- "$RESTORE_NEO4J_VOLUME" "$PROTECTED_SOURCE_VOLUMES"; then
  echo "Refusing to use protected source volume $RESTORE_NEO4J_VOLUME" >&2
  exit 1
fi
if test -n "$ACTIVE_SERVER_DIR"; then
  test -n "$ACTIVE_NEO4J_VOLUME"
  test "$RESTORE_NEO4J_VOLUME" != "$ACTIVE_NEO4J_VOLUME"
fi
printf 'Restore Neo4j snapshot only to: %s\n' "$RESTORE_NEO4J_VOLUME"
```

保持 Neo4j 停止，将主机或存储服务商的快照恢复准确指向
`RESTORE_NEO4J_VOLUME` 打印出的卷；绝不要手动输入或推测卷名。在同一个 shell 中
继续，确认项目标签仍解析到该卷，然后只将 history 恢复到替代检出目录并启动替代
服务：

```bash
test "$(docker volume ls -q \
  --filter "label=com.docker.compose.project=$RESTORE_PROJECT" \
  --filter label=com.docker.compose.volume=neo4j_data)" = "$RESTORE_NEO4J_VOLUME"
POSTGRES_CONTAINER="$(docker compose -p "$RESTORE_PROJECT" ps -aq postgres)"
NEO4J_CONTAINER="$(docker compose -p "$RESTORE_PROJECT" ps -aq neo4j)"
test -n "$POSTGRES_CONTAINER"
test -n "$NEO4J_CONTAINER"
RESOLVED_POSTGRES_VOLUME="$(docker inspect --format \
  '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' \
  "$POSTGRES_CONTAINER")"
RESOLVED_NEO4J_VOLUME="$(docker inspect --format \
  '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' \
  "$NEO4J_CONTAINER")"
test "$RESOLVED_POSTGRES_VOLUME" = "$RESTORE_VOLUME"
test "$RESOLVED_NEO4J_VOLUME" = "$RESTORE_NEO4J_VOLUME"
if grep -Fqx -- "$RESOLVED_POSTGRES_VOLUME" "$PROTECTED_SOURCE_VOLUMES" ||
   grep -Fqx -- "$RESOLVED_NEO4J_VOLUME" "$PROTECTED_SOURCE_VOLUMES"; then
  echo "Refusing to start services with a protected source volume" >&2
  exit 1
fi
test "$(pwd -P)" = "$RESTORE_SERVER_DIR"
test -z "$(find history -mindepth 1 -print -quit)"
tar --extract --gzip --file "$BACKUP_DIR/yiqiao-history.tar.gz" \
  --directory "$RESTORE_SERVER_DIR"
docker compose -p "$RESTORE_PROJECT" up -d neo4j yiqiao yiqiao-dashboard
```

选择性 `.env` 合并必须在这些服务启动前完成。API 只会应用比已恢复数据库更新的迁移。

切换流量前，请验证健康状态、登录、API 密钥创建、写入与检索、导出记录、图状态，
以及完整重启后的持久化。`pg_restore --clean` 和卷替换会销毁现有数据；上述命令只
适用于已经确认绝对目标路径和卷名的隔离替代部署。

## 升级

1. 阅读发行说明和迁移变更。
2. 记录镜像摘要并创建完整备份。
3. 通过快进拉取更新检出目录。
4. 将更新后的 `server/.env.example` 与现有 `server/.env` 比较，手动添加新要求的
   非密钥设置。初始化器会保留现有环境文件，只补充缺少的部署密钥；它不会合并任意
   模板键。
5. 再次运行初始化器，以补齐缺少的必需密钥并验证最终配置。
6. 拉取并重新创建容器。
7. 验证健康状态以及写入与检索冒烟测试。

```bash
git pull --ff-only
./scripts/init.sh
cd server
docker compose pull
docker compose up -d
docker compose ps
curl --fail http://localhost:8888/api/health
```

PowerShell 用户应从仓库根目录运行
`powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1`。API 容器启动时会自动
运行数据库迁移。不要中断迁移期间的首次启动。

## 回滚

应用回滚与数据回滚是两个不同的决策。只有在先前应用与已迁移架构兼容时，才能将
`YIQIAO_API_IMAGE` 和 `YIQIAO_DASHBOARD_IMAGE` 重新指向已记录的先前标签或摘要。
如果迁移不向后兼容，请创建替代部署，并恢复升级前的数据库和图备份。未经迁移审查，
绝不要让旧版本程序连接唯一一份新迁移的数据。

## 日志与健康状态

```bash
cd server
docker compose ps
docker compose logs --tail=200
docker compose logs --tail=200 yiqiao
curl --fail http://localhost:8888/api/health
```

请求日志可能包含提示词、标识符和元数据。请限制访问和保留时间，不要将原始日志粘贴
到公开 Issue 中。

## 卸载

删除应用容器但保留数据：

```bash
cd server
docker compose down
```

永久删除容器和具名数据库卷：

此操作绝不是迁移清理的一部分，不得用于保留的源、回滚卷、恢复克隆或证据卷。删除前
必须有成功的隔离恢复记录、准确的已批准卷清单和受保护源清单。批准清单每行包含一个
卷名。设置确认值前，应通过独立渠道审查打印出的名称。由于 `down -v` 还可能删除
连接的匿名卷，此流程会枚举实际容器挂载；只要任何卷缺少经审查的 Compose 项目和
角色标签，就会拒绝删除：

```bash
set -euo pipefail
cd server
VERIFIED_BACKUP_RECORD=/secure/path/to/verified-restore-record.txt
APPROVED_DELETE_VOLUMES=/secure/path/to/approved-delete-volumes.txt
PROTECTED_SOURCE_VOLUMES=/secure/path/to/protected-source-volumes.txt

: "${REMOVE_PROJECT:?Set REMOVE_PROJECT to the exact reviewed project}"
test -s "$VERIFIED_BACKUP_RECORD"
test -s "$APPROVED_DELETE_VOLUMES"
test -f "$PROTECTED_SOURCE_VOLUMES"

mapfile -t REMOVE_CONTAINERS < <(docker compose -p "$REMOVE_PROJECT" ps -aq)
test "${#REMOVE_CONTAINERS[@]}" -gt 0
for CONTAINER in "${REMOVE_CONTAINERS[@]}"; do
  test "$(docker inspect --format \
    '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER")" = \
    "$REMOVE_PROJECT"
done

mapfile -t PROJECT_VOLUMES < <(docker volume ls -q \
  --filter "label=com.docker.compose.project=$REMOVE_PROJECT")
mapfile -t ATTACHED_VOLUMES < <(docker inspect --format \
  '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{"\n"}}{{end}}{{end}}' \
  "${REMOVE_CONTAINERS[@]}" | sed '/^$/d')
mapfile -t REMOVE_VOLUMES < <(printf '%s\n' \
  "${PROJECT_VOLUMES[@]}" "${ATTACHED_VOLUMES[@]}" | sed '/^$/d' | sort -u)
test "${#REMOVE_VOLUMES[@]}" -gt 0
printf 'Project selected for permanent volume deletion: %s\n' "$REMOVE_PROJECT"
printf '  %s\n' "${REMOVE_VOLUMES[@]}"
diff -u \
  <(sort -u "$APPROVED_DELETE_VOLUMES") \
  <(printf '%s\n' "${REMOVE_VOLUMES[@]}")

for VOLUME in "${REMOVE_VOLUMES[@]}"; do
  VOLUME_PROJECT="$(docker volume inspect --format \
    '{{index .Labels "com.docker.compose.project"}}' "$VOLUME")"
  VOLUME_ROLE="$(docker volume inspect --format \
    '{{index .Labels "com.docker.compose.volume"}}' "$VOLUME")"
  if test "$VOLUME_PROJECT" != "$REMOVE_PROJECT" || test -z "$VOLUME_ROLE"; then
    echo "Refusing down -v: $VOLUME is anonymous or lacks the reviewed project/role labels" >&2
    exit 1
  fi
  if grep -Fqx -- "$VOLUME" "$PROTECTED_SOURCE_VOLUMES"; then
    echo "Refusing to delete protected source volume $VOLUME" >&2
    exit 1
  fi
done

: "${DELETE_PROJECT_CONFIRMATION:?Set it only after reviewing the printed project and volumes}"
test "$DELETE_PROJECT_CONFIRMATION" = "delete:$REMOVE_PROJECT"
docker compose -p "$REMOVE_PROJECT" down -v
```

只有在验证备份并解析出绝对路径后，才能删除 `server/history` 和 `server/.env`。
Compose 不会删除这些文件。

## 安全检查清单

- 保持 `AUTH_DISABLED=false`；生产覆盖配置会强制使用此值。
- 除非数据路径和接收方已经获批，否则保持遥测关闭。
- 保持 API 和控制台绑定到回环地址或私有接口。
- 所有远程访问都必须先终止 TLS。
- 怀疑发生泄露后，轮换 API 密钥、服务商密钥、数据库密码和 JWT 密钥。
- 将数据库转储、图快照、history 文件和请求日志视为敏感用户数据。
- 应用依赖项和基础镜像安全更新，并审查 SBOM。
- 定期测试恢复和重启持久化。
