# YiQiao
> **Modification notice:** This file was modified in 2026 by YiQiao contributors. See NOTICE.

**简体中文** | [English](README.md)

YiQiao 是面向 AI 助手与智能体的自托管记忆服务。它通过一套 Docker
Compose 部署，提供带身份验证的 REST API、运维控制台、语义记忆与图记忆、
聊天记录导入、数据导出、用量控制和 Webhook。集成通过项目级 API 密钥进行身份验证。

## 快速开始

运行前需要准备 Git、Docker Desktop 或安装了 Docker Compose v2 插件的
Docker Engine，并允许通过 HTTPS 拉取镜像以及访问初始化时选用的模型服务商。
Linux 和 macOS 下的 API 示例需要 curl 7.76 或更高版本。

Linux 和 macOS：

```bash
git clone https://github.com/daizhongming/YiQiao.git
cd YiQiao
./scripts/init.sh
cd server
docker compose up -d
```

Windows PowerShell：

```powershell
git clone https://github.com/daizhongming/YiQiao.git
Set-Location YiQiao
powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1
Set-Location server
docker compose up -d
```

打开 <http://localhost:3000>。首次运行向导会创建管理员、配置模型与嵌入服务商、
生成第一个项目 API 密钥，并执行一次记忆写入。初始化脚本会创建
`server/.env` 和高强度本地密钥；如果该文件已经存在，脚本不会覆盖它。

| 服务 | 默认地址 | 覆盖变量 |
| --- | --- | --- |
| 控制台 | <http://localhost:3000> | `DASHBOARD_PORT` |
| REST API | <http://localhost:8888> | `API_PORT` |
| MCP Streamable HTTP | <http://localhost:8765/mcp> | `MCP_PORT` |
| OpenAPI | <http://localhost:8888/docs> | 跟随 `API_PORT` |
| 健康检查 | <http://localhost:8888/api/health> | 跟随 `API_PORT` |

在 Linux 或 macOS 上确认整套服务已经就绪：

```bash
docker compose ps
curl --fail http://localhost:8888/api/health
```

在 Windows PowerShell 上：

```powershell
docker compose ps
Invoke-RestMethod -Uri "http://localhost:8888/api/health"
```

默认 Compose 配置会从 GitHub Container Registry 拉取正式发布镜像。如果需要从
当前检出的源码构建 API 和控制台镜像，请在 Linux 或 macOS 的仓库根目录运行：

```bash
cd server
docker compose -f docker-compose.yaml -f docker-compose.build.yaml up -d --build
```

Windows PowerShell：

```powershell
Set-Location server
docker compose -f docker-compose.yaml -f docker-compose.build.yaml up -d --build
```

<a id="verify-memory-add-and-search"></a>

## 验证记忆写入与检索

请先在浏览器中完成首次设置，并妥善保存向导仅显示一次的 API 密钥。默认项目标识为
`default-project`。

Linux 和 macOS：

```bash
export YIQIAO_API_URL=http://localhost:8888
export YIQIAO_API_KEY='<your-api-key>'
export YIQIAO_PROJECT_ID=default-project

curl --fail-with-body -X POST "$YIQIAO_API_URL/memories" \
  -H "X-API-Key: $YIQIAO_API_KEY" \
  -H "X-Project-ID: $YIQIAO_PROJECT_ID" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"I prefer concise answers."}],"user_id":"quickstart-user"}'

curl --fail-with-body -X POST "$YIQIAO_API_URL/search" \
  -H "X-API-Key: $YIQIAO_API_KEY" \
  -H "X-Project-ID: $YIQIAO_PROJECT_ID" \
  -H "Content-Type: application/json" \
  -d '{"query":"How should answers be written?","filters":{"user_id":"quickstart-user"}}'
```

Windows PowerShell：

```powershell
$apiUrl = "http://localhost:8888"
$headers = @{
  "X-API-Key" = "<your-api-key>"
  "X-Project-ID" = "default-project"
}
$addBody = @{
  messages = @(@{ role = "user"; content = "I prefer concise answers." })
  user_id = "quickstart-user"
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri "$apiUrl/memories" -Headers $headers -ContentType "application/json" -Body $addBody

$searchBody = @{
  query = "How should answers be written?"
  filters = @{ user_id = "quickstart-user" }
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri "$apiUrl/search" -Headers $headers -ContentType "application/json" -Body $searchBody
```

## Python 入口

安装已发布的 Python 包：

```bash
python -m pip install yiqiao
```

YiQiao 对外提供同步和异步两种 Python 入口：

```python
from yiqiao import Memory, AsyncMemory
```

本地状态默认保存在 `~/.yiqiao`，可通过 `YIQIAO_DIR` 指定其他目录。

需要以独立服务方式接入时，建议使用上面的 REST API 和项目 API 密钥。

## YiQiao 提供的能力

- 按项目隔离的记忆写入、检索、更新、删除、历史记录和反馈 API。
- 用于管理记忆、实体、关系图、请求记录、API 密钥、配置、用量限制、导入导出和
  Webhook 的控制台。
- 使用 PostgreSQL 与 pgvector 存储应用数据和向量数据，并可选用 Neo4j 保存图关系。
- 在浏览器中配置镜像内置的 LLM 与嵌入适配器，并支持自定义 OpenAI 兼容地址。
- 默认启用身份验证，支持管理员引导、项目 API 密钥、按角色控制工作区访问权限和
  请求日志。
- 支持带进度显示、重试、取消和存储配额的聊天记录导入。

典型使用场景包括持久化助手偏好、客户支持上下文、研究记忆、编码智能体上下文，
以及由使用者自主掌控存储和模型服务商关系的私有知识工作流。

## 架构

```text
浏览器 / API / MCP 客户端
        |
        +--> 控制台 :3000
        |         |
        +--> MCP companion :8765 --REST--+
        |                              |
        +---------+--------------------+--> YiQiao API :8888 --> 已选模型服务商
                             |  \
                             |   +--> Neo4j Community（图关系）
                             +------> PostgreSQL + pgvector（身份、设置、
                                      请求、向量和记忆元数据）
```

控制台的浏览器请求通过 `NEXT_PUBLIC_API_URL` 访问 API，服务端请求则使用
`API_INTERNAL_URL` 通过 Compose 内部网络通信。PostgreSQL 和 Neo4j 默认仅在内部
网络可见。数据库与图数据保存在具名 Docker 卷中；记忆历史 SQLite 数据库和导入
工作区位于 `server/history/`；部署配置和生成的密钥位于 `server/.env`。导出任务
记录与结果保存在 PostgreSQL 中，控制台下载的文件由客户端自行保存。配置远程模型
服务商后，相关请求会通过 HTTPS 离开当前部署。

## 配置

推荐通过 <http://localhost:3000/setup> 的首次运行向导完成配置。向导会读取 API
镜像内置的服务商列表，并允许管理员设置服务商、模型、基础 URL 和 API 密钥。
容器启动不依赖服务商凭据，但记忆提取和语义检索需要可用的 LLM 与嵌入配置。

部署设置位于 `server/.env`。生成的默认值会保持身份验证开启、遥测关闭、数据库
服务不映射到主机端口，并确保密钥不进入版本控制。不要将 API 或控制台直接暴露到
互联网；请通过可信的反向代理终止 TLS，并将访问限制在预期网络中。

stdio/Streamable HTTP 和安全捕获契约见
[MCP companion](docs/yiqiao/MCP.zh-CN.md)与
[Agent 集成](docs/yiqiao/AGENT_INTEGRATION.zh-CN.md)。端口、持久化、备份、
升级、源码构建和卸载说明见[运维指南](docs/yiqiao/OPERATIONS.zh-CN.md)。

## 文档

- [运维指南](docs/yiqiao/OPERATIONS.zh-CN.md)
- [MCP companion](docs/yiqiao/MCP.zh-CN.md)
- [Agent 集成](docs/yiqiao/AGENT_INTEGRATION.zh-CN.md)
- [迁移指南](docs/yiqiao/MIGRATION.zh-CN.md)
- [故障排查](docs/yiqiao/TROUBLESHOOTING.zh-CN.md)
- [许可与来源说明](docs/yiqiao/LEGAL.zh-CN.md)
- [安全策略](SECURITY.zh-CN.md)
- [贡献指南](CONTRIBUTING.zh-CN.md)

## 已知限制

- 默认部署是单主机 Compose 服务，不是高可用集群。
- Neo4j Community 不提供企业版的在线备份与集群能力；为图数据卷制作快照时需要安排
  维护窗口。
- 模型服务商的行为、隐私政策、速率限制和数据驻留由部署者负责评估和管理。
- `1.0.x` 是首个稳定兼容系列；跨主版本升级前请先阅读迁移说明。

当前路线图重点包括可复现的镜像来源与 SBOM、外部数据库部署文档、更完善的备份
自动化，以及版本化兼容策略。路线图只代表方向，不承诺交付日期。

## 许可证与第三方声明

YiQiao 是独立维护和发布的开源产品，采用 Apache License 2.0。许可证、第三方软件
归属和修改记录分别见 [LICENSE](LICENSE)、[NOTICE](NOTICE)、
[第三方声明](THIRD_PARTY_NOTICES.zh-CN.md) 和 [修改记录](MODIFICATIONS.zh-CN.md)。
