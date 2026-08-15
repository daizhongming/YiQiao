> **Modification notice:** This file was modified in 2026 by YiQiao contributors. See NOTICE.

# YiQiao 1.0.0

**简体中文** | [English](RELEASE_1.0.0.md)

YiQiao 1.0.0 是自托管 YiQiao 记忆服务的首个稳定版本。它将 REST API、运维
工作台、MCP 伴随服务、图记忆、聊天记录导入、导出、用量控制和 Webhook
统一到同一条版本化发布线上。

## 主要内容

- 建立稳定的 `1.0.x` 兼容系列，并统一 Python 包、REST API、工作台和 MCP
  伴随服务的版本号。
- 提供项目级 API 密钥认证、管理员初始化、按角色划分的工作区、请求日志、
  用量配额和 Webhook 投递。
- 提供语义记忆与图记忆流程，支持写入、检索、更新、删除、历史、反馈以及
  完全重复项清理。
- 聊天记录导入支持进度、重试、取消、存储配额、确定性恢复键和隔离的导入
  工作区状态。
- 为浏览器和智能体流程提供工作台与 MCP 契约覆盖，并对不可信召回内容明确
  标记安全警告。
- 在 CI 中验证可复现的 Compose 覆盖层、生产加固、法律载荷、依赖安全和
  中英文文档一致性。

## 兼容性与升级

YiQiao 1.0.0 保留 0.2.x 系列已有的 REST 资源模型和数据库迁移历史。升级前，
请按照[运维指南](OPERATIONS.zh-CN.md)完整备份 PostgreSQL 数据库、Neo4j 数据卷、
`server/history/` 和 `server/.env`。首次启动时请保持原有的嵌入模型和向量维度。

拉取带版本号的镜像或检出发布标签，然后使用生产加固覆盖层启动：

```bash
git checkout v1.0.0
cd server
docker compose -f docker-compose.yaml -f docker-compose.production.yaml up -d
docker compose ps
curl --fail http://localhost:8888/api/health
```

服务健康后，打开 `http://localhost:3000` 登录，确认服务商配置，并按根目录
README 中的示例完成记忆写入和检索。不要将 API、工作台、PostgreSQL 或 Neo4j
直接暴露到公网；请使用 TLS 反向代理或专用网络。

## 验证情况

发布候选版本已通过 Python 核心/服务端测试、MCP 伴随服务与契约测试、工作台
lint/typecheck/unit 测试、工作台生产构建、文档本地化、源码编译和包元数据检查。
可选服务商适配器仍需要各自的凭据和外部服务。

回滚和故障恢复请参阅[迁移指南](MIGRATION.zh-CN.md)、[故障排查](TROUBLESHOOTING.zh-CN.md)
和[运维指南](OPERATIONS.zh-CN.md)。
