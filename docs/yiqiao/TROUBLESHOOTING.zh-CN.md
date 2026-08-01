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

## 已下线的 OAuth 端点

OAuth 设备流程已在迁移 `019` 中移除。访问 `/oauth/*` 和原发现端点现在会返回 `404`。请改用项目 API 密钥，并通过 `X-API-Key` 请求头和目标 `X-Project-ID` 调用 REST API。

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
