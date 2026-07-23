# YiQiao 故障排查

**简体中文** | [English](TROUBLESHOOTING.md)

请在 `server/` 目录中运行诊断命令。日志和配置可能包含提示词、标识符、凭据及模型提供商详情，
请勿公开这些信息。

## 诊断顺序

```bash
docker compose config --quiet
docker compose ps
docker compose logs --tail=200
curl --fail-with-body http://localhost:8888/api/health
```

应先检查第一个不健康的依赖服务，不要反复重启整个服务栈。PostgreSQL 和 Neo4j 必须先恢复健康，
API 才能正常运行；API 健康后，仪表盘才能正常工作。

## 初始化程序报告缺少密钥

在仓库根目录运行当前平台对应的初始化程序：

```bash
./scripts/init.sh
```

PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1
```

初始化程序只会补充缺失的必需密钥。如果 `server/.env` 已存在，但其中包含格式错误或有意设置的
无效值，请先安全备份该文件，再与 `.env.example` 对比并修正相应值，不要直接删除来源不明的数据。

## 无法拉取镜像

检查容器注册表是否可访问，以及请求的镜像标签是否正确：

```bash
docker compose pull
```

公开镜像不需要注册表凭据。请在 GHCR 中确认软件包的公开状态和准确标签，或在不含注册表凭据的
一次性环境中测试匿名访问。不要在共享用户配置中运行 `docker logout`，否则还会删除其他私有软件包的
凭据。如果目标标签尚未发布或属于私有标签，请改用已授权的公开标签，或者从当前检出的源码构建：

```bash
docker compose \
  -f docker-compose.yaml \
  -f docker-compose.build.yaml \
  up -d --build
```

## 端口已被占用

使用主机的网络工具查找监听进程，或者在 `server/.env` 中修改仅绑定到本机回环地址的端口：

```dotenv
API_PORT=8889
DASHBOARD_PORT=3001
```

使用反向代理时，还需要更新 `PUBLIC_API_URL` 和 `PUBLIC_DASHBOARD_URL`。然后重新创建容器：

```bash
docker compose up -d --force-recreate
```

## PostgreSQL 不健康

```bash
docker compose logs --tail=200 postgres
docker compose exec postgres pg_isready -U postgres -d postgres
```

常见原因包括：现有数据卷中的密码与配置不一致、磁盘已满、手动复制数据卷后文件所有权错误，
或者恢复过程被中断。修改 `.env` 中的 `POSTGRES_PASSWORD` 不会改变已经初始化的数据卷中保存的密码。

除非数据可以丢弃，或者已经验证能够从备份恢复，否则不要通过删除数据卷来消除错误。

## Neo4j 不健康

```bash
docker compose logs --tail=200 neo4j
docker compose exec neo4j cypher-shell -u neo4j -p '<password>' 'RETURN 1'
```

请使用 `server/.env` 中的值；在共享系统中，避免让密码写入 shell 历史。常见原因包括现有数据卷的
密码不匹配、磁盘空间不足，或者复制的数据卷未正常关闭。Neo4j Community 在恢复后可能需要更长时间
才能进入健康状态；延长超时时间前应先检查日志。

## API 不健康或数据库迁移失败

```bash
docker compose logs --tail=300 yiqiao
docker compose exec yiqiao alembic current
docker compose exec yiqiao alembic heads
```

当前修订版本应到达仓库中唯一的迁移 head。不要仅为了让容器启动就强制标记或跳过失败的修订版本。
请恢复一份数据库测试副本，并在副本上复现迁移问题。

## 仪表盘能够打开，但 API 调用失败

分别确认浏览器可见地址和容器内部地址：

```bash
docker compose exec yiqiao-dashboard \
  wget -qO- http://yiqiao:8000/api/health
curl --fail http://localhost:8888/api/health
```

通过代理访问时，浏览器必须能够访问 `PUBLIC_API_URL`，而 `PUBLIC_DASHBOARD_URL` 必须与仪表盘的
外部来源地址一致。修改公开 URL 后，请重新创建仪表盘容器。

## 初始设置或登录失败

只有第一个账户可以使用注册端点。如果管理员已经存在，请直接登录，不要再次执行注册。检查 API
身份验证日志中的 `401`、`403` 或限流状态，同时避免暴露请求正文。

从 API 容器重置已知管理员的密码：

```bash
docker compose exec \
  -e EMAIL='admin@example.com' \
  -e PASSWORD='<new-strong-password>' \
  yiqiao python scripts/reset_admin_password.py
```

只能在可信终端中运行此命令。密码可能被 shell 或进程历史记录捕获；条件允许时，随后应在仪表盘中
再次轮换密码。

## API 密钥返回 401 或 403

- 首次创建密钥时复制完整内容；此后界面只会显示密钥前缀。
- 使用 `X-API-Key` 请求头发送密钥。
- 使用 `X-Project-ID` 请求头发送目标项目；默认项目为 `default-project`。
- 确认密钥尚未撤销，并且属于目标项目。
- 控制面设置必须使用管理员会话，不能使用项目密钥。

## 公共连接器发现或设备流程失败

先向配置的签发者发起只读请求；不要针对无关的在线部署进行测试：

```bash
curl --fail-with-body "$ISSUER/.well-known/oauth-authorization-server"
curl --fail-with-body "$ISSUER/.well-known/service-capabilities"
curl --fail-with-body "$ISSUER/api/health"
```

每个公布的 URL 都必须使用完全一致且可信的 `OAUTH_ISSUER` 来源。生产环境必须使用
HTTPS，并且必须等于 `PUBLIC_DASHBOARD_URL`。公共进程健康路由是 `/api/health`；
`/v1/ping/` 有意要求认证，不能替代进程健康检查。

如果经过控制台的连接器请求在到达 API 前失败，请确认控制台与 API 使用相同且非空的
`OAUTH_PROXY_HMAC_SECRET`，然后只重新创建这两个应用容器。绝不要输出该密钥或已签名
请求头。除非唯一入口网关把 `X-Forwarded-For` 替换为恰好一个经过验证的客户端 IP，
并实施等价的逐 IP 限流，否则应保持 `OAUTH_GATEWAY_RATE_LIMIT_CONFIRMED=false`；
透传或追加式代理不可信。

- `invalid_client`：确认公开客户端已预先登记、处于启用状态，并使用完全一致的
  `client_id`。
- `invalid_scope` 或 `invalid_target`：对照应用登记和能力文档检查请求的作用域与受众。
- `invalid_grant`：设备代码已过期或使用过、或者 PKCE verifier 不匹配时，丢弃该请求并
  重新开始设备流程；不得记录这两个值。
- `authorization_pending`：只保留一个轮询器，并遵守公布的间隔。遇到 `slow_down` 时
  增加间隔；遇到 `429` 时停止请求并遵守 `Retry-After`。
- `access_denied` 或 `expired_token`：重新发起设备请求，不要反复兑换旧代码。

令牌端点只支持设备代码兑换和刷新。客户端凭据授权与 RFC 8693 令牌交换按设计返回不支持
的授权类型错误。本版本没有 MCP Streamable HTTP 端点，因为它仍只是 ADR 评估项；不能
用它绕过 OAuth 或项目隔离。

从已退役的旧配对升级后，待处理配对请求会丢失，旧连接需要按需登记并通过设备流程重新
授权。降级无法恢复这些状态或逆转撤销操作；需要数据回滚时，应按照[迁移指南](MIGRATION.zh-CN.md)
恢复经过验证的升级前备份。

## OAuth 令牌或受保护资源返回错误

- `401 invalid_token` 通常表示令牌已过期、已撤销、格式错误、属于已停用应用，或者其
  授权不再有效。应重新授权，不要编辑持久化状态。
- `403 insufficient_scope`、`invalid_target` 或 `project_scope_mismatch` 表示路由、受众、
  作用域或项目与授权不匹配。删除冲突的 `X-Project-ID`、查询参数 `project_id`、记忆
  元数据项目或搜索过滤器项目；调用方不能覆盖令牌绑定的项目。
- 用户角色变化、项目成员资格移除或项目删除会在下一个请求立即生效。重新授权无法恢复
  用户已经失去的访问权。
- 重复使用已轮换的刷新令牌会撤销整个令牌族。停止所有并发刷新写入方，丢弃该令牌族，
  并重新完成设备流程。
- RFC 7009 撤销是幂等的，因此成功的空响应不能证明所提供令牌曾经存在。应在“已连接
  应用”中确认目标授权已不存在，并验证后续资源访问被拒绝。

诊断这些失败时，只使用关联标识和经过脱敏的 OAuth 审计事件。绝不要收集原始
Authorization 请求头、表单请求体、设备代码或用户代码、PKCE verifier、令牌、令牌
哈希或数据库记录。

## 无法连接模型提供商

在“配置”页面分别运行 LLM 和嵌入模型的连接测试。检查提供商名称、模型、基础 URL、API 密钥权限范围、
出站 DNS/TLS、速率限制和请求超时。兼容 OpenAI 的端点可能要求基础 URL 以 `/v1` 结尾；请遵循对应
提供商的接口约定。

通过仪表盘保存的提供商密钥会持久化到 PostgreSQL，重启容器不会清除它们。请通过经过身份验证的
配置流程替换或删除过期凭据，不要直接暴露数据库记录。

## 嵌入维度错误

配置的维度必须同时匹配提供商响应和现有向量集合。恢复原始模型和维度即可重新访问。修改现有数据的
维度需要创建新集合并执行明确的重新嵌入迁移；重启容器无法修复此问题。

## 搜索不到刚添加的记忆

1. 确认添加请求已经成功，而不是返回了提供商错误。
2. 添加和搜索时使用相同的项目及实体筛选条件。
3. 检查是否启用了信息提取，以及输入是否确实生成了事实。
4. 检查 API 请求日志和提供商连接测试。
5. 暂时移除阈值和重排序器进行搜索，以区分问题来自检索还是排序。

## 知识图谱为空或不可用

检查 Neo4j 健康状态、仪表盘中的图谱状态，以及是否启用了图记忆。即使图同步失败，向量记忆仍可能
存在。只有在修复连接或信息提取错误后，才能使用图谱重试或同步控制；反复重试可能消耗提供商配额。

## 聊天记录导入停滞或失败

检查导入任务阶段、错误、租约状态、已配置的提供商路由、可用历史存储空间、活动任务配额和 API 日志。
不要编辑活动任务工作区中的文件。修复根因后再重试失败的数据块；验证完成且不再需要源文件时，丢弃
保留的源文件。

## 磁盘占用持续增长

检查 Docker 和历史记录的磁盘占用：

```bash
docker system df
docker volume ls --filter label=com.docker.compose.project=yiqiao-v3
du -sh history 2>/dev/null || true
```

手动清理前，优先使用仪表盘中的保留策略和导入丢弃控制。不要在承载其他工作负载的主机上运行大范围
Docker 清理命令。删除前必须完成备份，并明确要操作的绝对路径或数据卷。

## 收集支持信息

YiQiao 不会自动生成所谓“安全”的支持信息包。请记录提交版本、镜像摘要、`docker compose ps`、失败命令、
状态码，以及经过最小化和脱敏的日志片段。公开提交问题时，不得包含 `.env`、数据库转储、提供商配置、
API 密钥、JWT、Cookie、提示词、导入文件或个人数据。
