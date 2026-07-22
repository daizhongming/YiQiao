# 公共服务连接器
> **Modification notice:** This file was modified in 2026 by YiQiao contributors. See NOTICE.

**简体中文** | [English](PUBLIC_CONNECTOR.md)

YiQiao 以通用 OAuth 边界实现公共服务连接器协议 `1.0`。已登记的公开客户端通过带
PKCE S256 的设备授权流程获得绑定项目的不透明 Bearer 令牌，并且只能调用能力文档
公布的记忆资源。现有项目 API 密钥仍属于独立的身份验证机制。

服务绝不会根据产品名称或 `client_id` 选择业务行为。新增客户端只需要创建应用记录，
不需要新增路由、控制器、策略分支或页面。

## 公共签发者

在受支持的 Compose 部署中，`OAUTH_ISSUER` 是权威连接器签发者，并且必须等于
`PUBLIC_DASHBOARD_URL`。控制台在该来源公开发现、OAuth、健康检查和记忆路径，并通过
`API_INTERNAL_URL` 在服务端将其代理到 API。控制台的普通 API 流量仍可使用
`PUBLIC_API_URL`。

生产部署必须显式设置 HTTPS `OAUTH_ISSUER` 和与其相同的 `PUBLIC_DASHBOARD_URL`，
且地址不得包含凭据、路径、查询参数或片段。只有 API 服务器套接字被确认位于回环开发
环境时，才允许省略签发者。反向代理必须保留公共协议与主机、拒绝意外重定向，并将下列
所有连接器路径路由到同一控制台部署。不要把内部 API 来源作为第二个签发者公开。

初始化脚本会分别生成 `OAUTH_USER_CODE_HMAC_SECRET` 与
`OAUTH_AUDIT_HMAC_SECRET`。前者保护低熵用户代码查找，后者为共享审计强制策略哈希
IP、用户代理和限流上下文。它们都是必需密钥，必须与 `JWT_SECRET` 不同，并且恢复应用
数据库时必须使用相同的值。

客户端只能从编译时信任的签发者开始引导，并获取：

- `GET /.well-known/oauth-authorization-server`
- `GET /.well-known/service-capabilities`

这两份文档在签发者来源公布以下规范路径：

| 用途 | 路径 |
| --- | --- |
| 设备授权 | `/oauth/device_authorization` |
| 令牌交换与刷新 | `/oauth/token` |
| 令牌撤销 | `/oauth/revoke` |
| 连接器健康检查 | `/oauth/health` |
| 用户验证 | `/dashboard/connected-apps` |
| 记忆检索 | `/search` |
| 记忆写入 | `/memories` |
| 已认证 Ping | `/v1/ping/` |

能力契约固定使用服务 ID `yiqiao`、受众 `yiqiao:memory-api`、作用域
`memory:read` 与 `memory:write`，以及协议版本 `1.0`。设备代码有效期为 600 秒，访问
令牌为 900 秒，刷新令牌为 2,592,000 秒。客户端遇到不同的签发者、来源、规范路径或
不支持的协议主版本时必须按封闭失败处理。

## 应用登记

管理员通过“已连接应用”控制台或经过身份验证的 `/oauth/applications` API 管理纯数据
应用记录。公开应用包含稳定的 `client_id`、显示名称、允许的受众与作用域、状态，以及
可选但不会被解释为执行策略的运维元数据。公开客户端没有客户端密钥。

只向客户端授予所需的最小作用域。撤销应用后，新的授权会被拒绝，其有效授权也会失效。
管理响应只公开可安全显示的标识符、前缀、状态、作用域、项目和时间戳；绝不公开令牌
哈希、代码哈希、明文凭据或内部限流键。

## 设备授权

客户端向发现到的设备端点发送表单编码的 `client_id`、`scope`、`audience`、
`code_challenge` 和 `code_challenge_method=S256`。响应包含一次性设备代码、供用户
输入的代码、验证 URI、有效期和轮询间隔。

已登录用户打开验证页面，核对应用、受众、请求的作用域和有效期，然后选择自己可访问的
项目并批准或拒绝请求。批准时可以缩减作用域，但不能增加未申请或未登记的作用域。在请求
可交换之前，令牌端点会返回 `authorization_pending`、`slow_down`、`access_denied`
或 `expired_token`。

签发的 Bearer 令牌绑定到一个用户、应用、受众、作用域集合和项目。OAuth 令牌只能用于
精确的 `POST /memories`、`POST /search` 和 `GET /v1/ping/` 请求。资源服务器会拒绝
通过请求头、查询参数或请求体覆盖项目绑定，也会拒绝过期或已撤销凭据、已停用应用、
不可访问项目、错误受众和缺少作用域的请求。

## 刷新与撤销

每次成功刷新都会轮换访问令牌和刷新令牌。重复使用已经轮换的刷新令牌会撤销整个令牌族。
客户端必须串行写入凭据，并合并并发刷新请求。

RFC 7009 撤销端点接受访问令牌或刷新令牌。撤销刷新令牌会撤销其令牌族；未知令牌返回
成功的空响应。用户也可以在“已连接应用”中撤销单个授权，或撤销某应用与项目的全部有效
授权。撤销状态由共享 PostgreSQL 校验，会在所有 API 进程上立即生效。

## 运维

Alembic 修订版 `018` 创建通用 OAuth 表、移除仍在使用的旧配对状态，并且只撤销标记为
已退役配对密钥类型的凭据。修订版 `017` 在迁移历史中保持字节不变，以便现有安装安全
升级。升级前应备份应用 PostgreSQL 数据库；不得跳过 `018` 强行标记版本，也不得让旧版
API 连接已经迁移的数据库。

过期设备请求、为检测重放而保留的刷新令牌哈希、旧授权和审计事件会按有限批次清理。请在
日常维护中安排以下命令：

```text
cd server
make prune-oauth
```

`OAUTH_CLEANUP_BATCH_SIZE` 默认为 `500`，`OAUTH_AUDIT_RETENTION_DAYS` 默认为
`90`，`OAUTH_REFRESH_REPLAY_GRACE_SECONDS` 默认为 `86400`。重放保留时间应足以支持
事件检测；审计记录应按照部署的安全与隐私策略保留。清理命令不会撤销仍有效的授权。

## 安全控制

- 保持 `AUTH_DISABLED=false`。部署关闭正常用户身份验证时，连接器授权不可用。
- 将 PostgreSQL 及其备份视为敏感凭据状态。设备值、访问令牌和刷新令牌只以单向哈希
  保存；用户代码熵较低，因此使用带密钥哈希查找。
- 在可信代理终止 TLS，只允许预期的签发者主机，并且不得接受页面内容或导入数据提供的
  签发者与端点覆盖值。
- 不得记录 Authorization 请求头、表单请求体、设备代码、用户代码或令牌。OAuth 与发现
  响应使用 `Cache-Control: no-store`。
- 在每个副本上保持基于数据库的 OAuth 限流。进程内存限流不能替代共享强制策略。
- 将“已连接应用”访问视为高权限控制台操作。定期审查应用登记、有效授权、拒绝或重放
  事件以及清理保留策略。

## 故障排查

首先将两份发现文档与配置的签发者进行比较。每个 URL 都必须使用相同来源和规范路径。
生产启动时出现签发者错误，通常表示 `OAUTH_ISSUER` 缺失、与
`PUBLIC_DASHBOARD_URL` 不完全一致、不是 HTTPS，或包含部署无法一致提供服务的路径部分。

遇到 `invalid_client` 时，确认应用处于有效状态且 `client_id` 完全匹配。遇到
`invalid_scope` 或 `invalid_target` 时，对照应用记录检查请求作用域与受众。持续收到
`slow_down` 时，应停止多余轮询器并遵守返回的时间间隔。遇到 `access_denied` 或
`project_scope_mismatch` 时，重新发起设备请求，并批准用户仍可访问的项目。检测到刷新
令牌重放后，令牌族会被有意撤销，必须重新完成设备授权。

诊断时只使用关联标识和经过脱敏的 OAuth 审计事件。绝不要在公开 Issue 中粘贴凭据、
原始请求体、数据库记录或代理日志。
