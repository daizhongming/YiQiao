# 密钥扫描

**简体中文** | [English](SECURITY_AUDIT.md)

YiQiao 使用 Gitleaks 8.28.0 扫描当前源代码树和完整的公开 Git 历史。发布历史从目标仓库的占位提交
开始，并将审查过的 YiQiao 源码作为单个快照加入；发布仓库不会合并或保留来源仓库的提交图。

公开版本不包含 `.gitleaksignore`。YiQiao 的每个提交都必须在不使用提交、路径、规则或行指纹例外的
情况下通过扫描。`.gitleaks.toml` 中的窄范围规则只允许已检入环境变量模板中相邻且值为空的提供商
密钥项；只要填写其中任意一个值，该规则就不再匹配。

使用以下命令执行发布检查：

```bash
gitleaks dir --redact=100 --no-banner .
gitleaks git --redact=100 --no-banner .
```

调查扫描失败时，请在仓库外生成完全脱敏的报告：

```bash
gitleaks git --redact=100 --no-banner --report-format json \
  --report-path /tmp/yiqiao-gitleaks-review.json .
```

如果 Gitleaks 生成了包含发现项的报告，它会以状态码 1 退出。应修复或删除检测到的内容；不要仅为了
让 CI 通过就添加指纹例外。干净的检出不包含 `.env`、`server/history`、日志、浏览器产物、数据库
转储和备份等运行时状态；这些内容绝不能提交或加入允许列表。

## 公共连接器安全审查

密钥扫描不能证明 OAuth 边界正确。除非针对真实 API 和共享 PostgreSQL 状态的审查证据
覆盖以下全部控制，否则连接器版本或部署均为 **NO-GO**：

- 生产环境的 `OAUTH_ISSUER` 必须显式使用 HTTPS、与控制台外部来源完全一致，并且不得
  从不可信的 `Host` 或转发请求头派生。发现与能力 URL 必须保持在这一个可信来源上。
- `/api/health` 是无需认证的进程健康端点。`GET /v1/ping/` 是受保护资源，需要具有
  记忆作用域且绑定项目的有效 OAuth 令牌。只有精确的 `POST /memories`、
  `POST /search` 和 `GET /v1/ping/` 资源路由接受连接器令牌。
- 设备授权流程要求预先登记且处于启用状态的公开客户端、表单编码、已登记的受众与
  作用域，以及 PKCE S256。批准时可以缩减作用域，但不能增加作用域。
- 明文设备代码、访问令牌和刷新令牌只能出现在创建它们的那次响应中。PostgreSQL 只
  保存单向哈希。低熵用户代码查找使用 `OAUTH_DEVICE_CODE_SECRET`；该密钥必须独立于
  `JWT_SECRET`、`OAUTH_AUDIT_HMAC_SECRET`、`OAUTH_PROXY_HMAC_SECRET` 和
  `ADMIN_API_KEY`；三个 OAuth HMAC 密钥必须彼此独立，生产环境不得回退到其他密钥。
- 每个受保护请求都必须重新检查应用与授权状态、有效期、受众、作用域、绑定项目以及
  用户当前的项目角色。请求头、查询参数、请求体、元数据或搜索过滤器中的项目覆盖值
  必须按封闭失败处理。
- 刷新轮换必须是原子操作。重复使用已轮换的刷新令牌会撤销整个令牌族，并且撤销状态
  必须通过共享状态在所有 API 进程中生效。失败请求不得更新成功使用时间。
- 设备授权、令牌、撤销、用户代码查询与批准以及应用登记必须按相关 IP、客户端和待处理
  授权数量实施共享、事务性限流。`429` 必须包含 `Retry-After`。进程内存限流不能证明
  多副本强制策略有效。
- 控制台必须删除调用方提供的转发头和内部上下文头，并使用
  `OAUTH_PROXY_HMAC_SECRET` 对规范化传输对端、方法、路径与查询参数以及时间戳签名；
  API 必须拒绝不完整、过期或被篡改的上下文。只有当唯一入口网关把
  `X-Forwarded-For` 替换为恰好一个经过验证的客户端 IP，并实施等价的逐 IP 限流时，
  才能设置 `OAUTH_GATEWAY_RATE_LIMIT_CONFIRMED=true`；透传或追加式代理必须保持为
  `false`。
- 审计事件和应用管理响应不得包含凭据、令牌或代码哈希、PKCE verifier、Authorization
  请求头、表单请求体、原始 IP 或其他密钥。保留与分批清理策略必须在配置的宽限期内
  保存刷新令牌重放证据。

验证隔离时应使用两个互不相关的已登记公开客户端，并提供错误 PKCE、作用域、受众、
跨客户端和跨项目使用、项目覆盖、角色变化、撤销、重放、限流、清理以及并发兑换、刷新
和撤销的负向证据。SQLite 只用于快速本地检查；并发与共享强制策略必须使用专用、隔离的
PostgreSQL 测试数据库。

## 授权与传输边界

已实现的用户流程是 OAuth 设备授权与刷新令牌轮换。客户端凭据授权
（`grant_type=client_credentials`）和 RFC 8693 令牌交换属于服务间设计，本版本未实现。
存在 `/oauth/token` 并不表示支持这两种流程。

MCP Streamable HTTP 仅属于 ADR 评估材料。本版本没有 MCP 传输或端点。未来任何 MCP
设计仍须遵守相同的 OAuth 检查和项目隔离；MCP 路径绝不能绕过它们。协议契约见
[公共连接器](PUBLIC_CONNECTOR.zh-CN.md)，安全诊断见[故障排查](TROUBLESHOOTING.zh-CN.md)。
