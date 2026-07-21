# YiQiao 服务端
> **Modification notice:** This file was modified in 2026 by YiQiao contributors. See NOTICE.

**简体中文** | [English](README.md)

本目录包含 YiQiao API、控制台、数据库迁移和 Docker Compose 部署配置。产品的标准
快速开始流程见[仓库 README](../README.zh-CN.md)。

## 快速开始

请从仓库根目录运行初始化脚本。脚本会复制 `server/.env.example`，分别生成
`POSTGRES_PASSWORD`、`NEO4J_PASSWORD` 和 `JWT_SECRET`，并保留已经存在的
`.env`。服务商凭据通过浏览器配置，不会阻止容器启动。

Linux 和 macOS：

```bash
./scripts/init.sh
cd server
docker compose up -d
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1
Set-Location server
docker compose up -d
```

打开 <http://localhost:3000> 并完成首次运行设置。默认服务地址如下：

| 服务 | 地址 | 覆盖变量 |
| --- | --- | --- |
| 控制台 | <http://localhost:3000> | `DASHBOARD_PORT` |
| API | <http://localhost:8888> | `API_PORT` |
| OpenAPI | <http://localhost:8888/docs> | 跟随 `API_PORT` |
| 健康检查 | <http://localhost:8888/api/health> | 跟随 `API_PORT` |

默认部署会拉取配置的正式发布镜像。如需从当前源码构建 API 和控制台，请在仓库根目录
通过 Bash 或 PowerShell 运行：

```text
cd server
docker compose -f docker-compose.yaml -f docker-compose.build.yaml up -d --build
```

`make` 目标只是可选的维护快捷方式，安装 YiQiao 不依赖它们。

## 安全与持久化

身份验证默认开启，遥测默认关闭。API 和控制台绑定到回环地址，PostgreSQL 与 Neo4j
只连接内部后端网络，不映射主机端口。不要将这些服务直接暴露到互联网；请使用可信的
TLS 反向代理或私有网络。

持久化的应用数据、向量数据和导出记录保存在 `postgres_db` 卷中，图数据保存在
`neo4j_data` 卷中，记忆历史 SQLite 数据库和导入工作区位于 `server/history/`。
控制台下载的文件保存在客户端。请将 `server/.env`、数据库备份、图快照、历史文件、
服务商凭据和请求日志视为敏感数据。

数据库迁移会在 API 启动前自动运行。迁移失败时必须停止启动；不要跳过失败的迁移，
也不要让旧版本 API 连接唯一一份已经迁移的数据。

## 文档

- [运维、备份、升级、回滚和卸载](../docs/yiqiao/OPERATIONS.zh-CN.md)
- [迁移指南](../docs/yiqiao/MIGRATION.zh-CN.md)
- [故障排查](../docs/yiqiao/TROUBLESHOOTING.zh-CN.md)
- [安全策略](../SECURITY.zh-CN.md)
- [API 写入与检索示例](../README.zh-CN.md#验证记忆写入与检索)
