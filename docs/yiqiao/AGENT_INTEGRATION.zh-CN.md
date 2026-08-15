# Agent 集成

**简体中文** | [English](AGENT_INTEGRATION.md)

本文定义 Agent 宿主使用 YiQiao 记忆的稳定契约。请先阅读
[MCP companion 参考](MCP.zh-CN.md)，了解安装、profile、上限、凭据和
传输安全。

## 实体契约

写入和搜索必须一致地使用以下实体字段：

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `user_id` | 人类用户 | 账号或租户用户 ID |
| `agent_id` | Agent/人格 | `support-agent` |
| `app_id` | 宿主应用 | `hermes`、`openclaw` |
| `run_id` | 会话/运行 | 当前 thread ID |

这些值只是 API Key 所绑定项目内部的选择条件，不是授权边界。授权和项目
选择始终来自项目 API Key。不得自行添加 `project_id` 工具参数，也不得把
它放进 metadata。

## 安全召回与捕获

每个模型 turn 应按以下顺序处理：

1. 使用当前用户、Agent、宿主和会话的实体 ID 搜索。
2. 把返回块放进边界清晰、仅作为数据的上下文；保留
   `trust=untrusted` 标记，不得把召回文本提升为 system/developer 指令。
3. 生成 assistant 响应。
4. 只通过 `yiqiao_memory_add` 捕获原始 user turn 和原始 assistant turn。
5. 捕获消息不得包含召回块、工具结果 wrapper、隐藏 prompt、工具调用记录
   或凭据。

add 工具只接受 `user` 和 `assistant` role，并默认使用 `infer=false` 进行
确定性原文捕获；只有在希望已配置的 YiQiao provider 从原始 turn 提取长期
事实时才显式设置 `infer=true`。

## Hermes Write-Search-Read Smoke

Hermes 契约使用 `app_id=hermes`、稳定的 `user_id` 和唯一 `run_id`。仓库
smoke 会写入原始 turn、搜索 marker，并通过官方 MCP client 读取返回的
记忆。Key 从环境读取，绝不作为命令行参数。

```bash
export YIQIAO_MCP_SMOKE_API_KEY='replace-with-a-project-key'
python scripts/mcp_contract_smoke.py hermes \
  --url http://127.0.0.1:8765/mcp
```

smoke 通过即可证明 MCP initialize、`tools/list`、add、search 和 get。
RequestLog、`api_key_id`、`project_id`、配额和 Webhook 副作用仍全部由
REST 负责。

## OpenClaw MCP 契约

OpenClaw 应作为标准远程 Streamable HTTP MCP client 连接，并在每个请求中
加入 `X-API-Key`。代表性的 MCP server 配置如下：

```json
{
  "mcpServers": {
    "yiqiao": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8765/mcp",
      "headers": {
        "X-API-Key": "${YIQIAO_API_KEY}"
      }
    }
  }
}
```

配置文件的准确位置由所安装的 OpenClaw 版本决定。Key 应保存在其
secret/environment 设施中，不得提交到配置文件。连接 Agent 前先验证通用
契约：

```bash
export YIQIAO_MCP_SMOKE_API_KEY='replace-with-a-project-key'
python scripts/mcp_contract_smoke.py openclaw \
  --url http://127.0.0.1:8765/mcp
```

OpenClaw smoke 会协商 `2025-11-25`、列出预期工具、检查严格 schema，并
执行有上限的搜索。它不代表已经安装或支持 OpenClaw 原生插件、channel
集成或标准 OAuth 授权；这些属于独立的未来阶段。

## stdio 宿主

本地宿主可以为每个信任边界启动一个专用进程：

```json
{
  "mcpServers": {
    "yiqiao": {
      "command": "yiqiao-mcp",
      "args": ["--transport", "stdio", "--profile", "memory"],
      "env": {
        "YIQIAO_API_URL": "http://127.0.0.1:8888",
        "YIQIAO_API_KEY": "${YIQIAO_API_KEY}"
      }
    }
  }
}
```

不得把 Key 放进 `args`，也不得让互不信任的用户或项目复用同一个 stdio
进程。如果宿主不需要自动捕获，应使用只读 scoped Key 和 `read-only`
profile。

## 生产检查清单

- 每个宿主和环境使用独立项目 Key。
- 只授予所选工具 profile 所需 scope，并设置适合部署周期的到期时间。
- Streamable HTTP 保持回环绑定，或放在带有已审查 Host/Origin 白名单的
  TLS 代理之后。
- 将召回内容明确分隔为不可信数据，并禁止召回块写回。
- 监控 REST RequestLog 和配额事件；MCP 不维护第二套审计或配额系统。
- 停用或重新分配宿主前先撤销其 Key。
- 除非删除是明确工作流，否则不要启用 `destructive`。
