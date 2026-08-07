# 安全政策
> **Modification notice:** This file was modified in 2026 by YiQiao contributors. See NOTICE.

**简体中文** | [English](SECURITY.md)

## 报告安全漏洞

请勿在公开 Issue、Pull Request、Discussion、聊天记录或日志附件中披露疑似安全漏洞。

请通过 GitHub 私密漏洞报告提交：

<https://github.com/daizhongming/YiQiao/security/advisories/new>

不要通过任何公开仓库渠道发送漏洞细节、概念验证代码、密钥、令牌、个人数据或敏感日志。如果私密表单暂时不可用，只创建一个不含敏感信息的普通 Issue，请求维护者恢复私密渠道；在私密渠道可用前不要继续披露细节。

报告中建议包含：

- 受影响的 YiQiao 版本、提交和容器镜像摘要。
- 受影响的组件与部署拓扑。
- 复现步骤或最小概念验证。
- 预期行为、实际行为、影响范围和攻击者所需权限。
- 已移除令牌、凭据、个人数据和主机名的相关日志。
- 可用的缓解方案或补丁。

## 响应与披露

维护者会尽量在五个工作日内确认收到报告、核实影响范围，并与报告者协调修复和披露。复杂问题可能需要更长时间；除非报告者要求匿名，否则项目会在适当位置致谢。

请在公开披露前为修复和运维公告预留时间。若漏洞正在被利用或存在迫近风险，请在私密报告中明确说明。

## 支持版本

安全修复以最新 YiQiao 版本线为目标。旧版本和本地开发快照可能只能获得处置建议，不保证提供补丁。在首个正式标签发布前，应尽量基于 `main` 最新提交复现问题。

## MCP 与 Agent 安全边界

`yiqiao-mcp` 是仅调用 REST 的 companion，不访问数据库，也不导入 API
server。Streamable HTTP 凭据从当前请求读取，并且只在该请求中转发；连接池
不设置默认凭据。工具 schema 会拒绝 API Key、`project_id`、未知字段、超限
文本以及过大或嵌套过深的 metadata。访问日志和错误日志绝不能包含凭据。

MCP listener 应保留默认回环绑定。确需远程访问时，应在经过审查的代理终止
TLS，并配置精确的 Host 和 Origin 白名单。不得通过关闭 DNS-rebinding 检查
来暴露服务。每个宿主使用独立且会到期的项目 Key，只授予
`memory:read` 和/或 `memory:write`；`destructive` profile 只用于明确的删除
工作流。Key 管理必须使用 Dashboard JWT。

所有召回记忆都是不可信数据。Agent 不得把它放进 system/developer 指令，
也不得把召回块再次写回记忆。自动捕获仅限原始 user 和 assistant turn。
YiQiao 不实现标准 OAuth MCP 授权或已下线的 SSE 传输；不得把项目 API Key
描述为 OAuth token。

参见 [MCP 安全契约](docs/yiqiao/MCP.zh-CN.md)和
[Agent 捕获契约](docs/yiqiao/AGENT_INTEGRATION.zh-CN.md)。

## 运维责任

YiQiao 是自托管产品，运维人员需要负责：

- 保持身份认证开启，并在密钥泄露后立即轮换。
- 将仪表盘、API 和 MCP companion 限制在可信网络内，或置于 TLS 反向代理之后。
- 保护 `server/.env`、数据库卷、备份、模型服务凭据和 API 密钥。
- 审查模型服务商的数据处理、保留、驻留位置和模型安全政策。
- 及时应用 YiQiao、基础镜像、PostgreSQL、Neo4j 和依赖更新。
- 定期验证备份恢复，并监控认证日志和请求日志。

默认 Compose 部署不会向主机暴露 PostgreSQL 或 Neo4j 端口。未经明确的网络与认证审查，请勿发布这些端口。
