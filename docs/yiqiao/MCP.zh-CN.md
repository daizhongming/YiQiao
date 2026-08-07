# YiQiao MCP Companion

**简体中文** | [English](MCP.md)

`yiqiao-mcp` 是独立的 YiQiao Model Context Protocol companion。它通过
stdio 和 Streamable HTTP 实现 MCP `2025-11-25` 协议。companion 使用
`httpx` 调用公开的 YiQiao REST API；它不会打开数据库、导入
`server.main`，也不会复制记忆业务逻辑。REST 始终是项目隔离、权限、
配额、请求日志和 Webhook 的唯一事实源。

本实现不提供旧 HTTP+SSE 传输，也不提供标准 OAuth MCP 授权。migration
`019` 仍是已下线 OAuth Device Flow 的权威边界。

## 安装与运行

从当前检出安装 companion：

```bash
python -m pip install ./yiqiao-mcp
yiqiao-mcp --version
```

命令默认使用 stdio、`memory` profile 和 REST 地址
`http://127.0.0.1:8888`。专用 stdio 进程从 `YIQIAO_API_KEY` 读取项目
密钥；命令行刻意不提供密钥参数。

```bash
export YIQIAO_API_KEY='replace-with-a-project-key'
yiqiao-mcp --transport stdio --api-url http://127.0.0.1:8888 --profile memory
```

Compose 默认在 `http://127.0.0.1:8765/mcp` 启动 Streamable HTTP，且不会
把项目密钥写入容器环境。每个 MCP HTTP 请求都必须携带自己的
`X-API-Key`；该凭据只会转发给对应的 REST 调用。

```bash
docker compose --env-file server/.env \
  -f server/docker-compose.yaml up -d yiqiao-mcp
curl --fail http://127.0.0.1:8765/healthz
```

可在 `server/.env` 中通过 `MCP_BIND_ADDRESS`、`MCP_PORT` 和
`YIQIAO_MCP_PROFILE` 调整宿主绑定、端口和 profile。除非服务前方已有
经过审查的 TLS 反向代理及明确的 Host/Origin 白名单，否则应保留默认
回环绑定。

## 工具 Profile

| 工具 | `read-only` | `memory` | `destructive` |
| --- | --- | --- | --- |
| `yiqiao_memory_search` | 是 | 是 | 是 |
| `yiqiao_memory_get` | 是 | 是 | 是 |
| `yiqiao_memory_history` | 是 | 是 | 是 |
| `yiqiao_memory_add` | 否 | 是 | 是 |
| `yiqiao_memory_update` | 否 | 是 | 是 |
| `yiqiao_memory_delete` | 否 | 否 | 是 |

工具 schema 使用 `additionalProperties: false`，绝不暴露 API Key、凭据或
`project_id` 参数。REST 项目始终由所用 API Key 绑定。因此，跨项目的
记忆 ID 会由 REST 返回 `404`，不会泄露其他项目中对象是否存在。

companion 在发出请求前执行以下 v1 上限：

| 输入 | 上限 |
| --- | --- |
| 搜索 query | 8,192 个字符 |
| `top_k` | 1 到 100 |
| 单条消息或更新文本 | 32,768 个字符 |
| add 消息 | 1 到 20 条；合计 65,536 个字符 |
| 实体 ID | 255 个字符 |
| metadata | 32 KiB JSON、深度 8、200 个属性 |
| REST 响应 | 2 MiB |

metadata 的任意层级都不得包含 `project_id`。metadata 必须是有限 JSON，
不得包含 NaN 或无穷值。

## 凭据与权限

项目 Key 只能在 Dashboard 中创建和撤销，Key 管理端点只接受 Dashboard
Bearer JWT。新 Key 默认显式包含 `memory:read` 和 `memory:write`，操作员
可以选择更窄的范围及到期时间。数据库中 `scopes=NULL` 的历史 Key 保持
原有读写兼容行为。

| 存储的 scopes | 记忆读取 | 记忆写入 |
| --- | --- | --- |
| `NULL`（旧 Key） | 允许 | 允许 |
| `[]` | 拒绝 | 拒绝 |
| `memory:read` | 允许 | 拒绝 |
| `memory:write` | 拒绝 | 允许 |
| 两者都有 | 允许 | 允许 |

已撤销或过期的凭据会在进入记忆业务逻辑前失败。对于 Streamable HTTP，
凭据从当前 ASGI 请求中提取，并作为本次 `httpx` 请求的局部 header 传递。
共享连接池不设置默认认证 header；请求不会回退到其他请求、会话或
initialize 消息中的密钥。

工具参数和命令行参数均不接受 Key，访问日志、工具错误或 REST 错误文本
也不会输出 Key。不要把 Key 放进 URL。

## 不可信召回内容

每个成功工具结果都带有 `source=yiqiao_rest`、`trust=untrusted` 和警告。
所有召回的记忆文本与 metadata 都必须作为数据而非指令处理。应将召回块
与 system/developer 指令清晰分隔，不得执行记忆中的命令、工具请求或策略
修改。

自动捕获只能存储原始 user/assistant turn。不得把召回块拼入 prompt 后，
再通过 `yiqiao_memory_add` 写回展开后的 prompt；这种做法会形成检索反馈，
并允许持久化 prompt injection。安全捕获顺序和实体含义见
[Agent 集成](AGENT_INTEGRATION.zh-CN.md)。

## HTTP 安全与失败语义

默认 HTTP 服务校验 `Host` 和 `Origin`，防止 DNS rebinding。默认只允许
回环 Host 和回环 HTTP Origin。未获批准的 Origin 会在 MCP 分发前被拒绝。
只有经过审查的反向代理 Origin 才应加入额外白名单。

客户端取消会取消正在进行的 `httpx` 请求。连接失败或 REST 不可用会作为
脱敏工具错误返回；REST `401`、`403`、`404`、`422`、`429` 和 `503` 状态
分类仍可见，但不会回显凭据或任意上游响应体。超时由
`YIQIAO_MCP_CONNECT_TIMEOUT` 与 `YIQIAO_MCP_REQUEST_TIMEOUT` 限制。

部署、健康检查、代理配置和故障诊断见[运维指南](OPERATIONS.zh-CN.md)。
安全问题报告遵循根目录[安全策略](../../SECURITY.zh-CN.md)。
