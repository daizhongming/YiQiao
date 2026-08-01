> **Modification notice:** This file was modified in 2026 by YiQiao contributors. See NOTICE.

# YiQiao 0.2.0

**简体中文** | [English](RELEASE_0.2.0.md)

YiQiao 0.2.0 是自托管版本，不提供也不依赖线上托管的 YiQiao 工作台服务。
使用者需要在自己的环境中部署 API、工作台、Postgres 与 Neo4j。

## 本次包含

- Public Service Connector 1.0：OAuth Device Flow、PKCE、范围化授权、令牌轮换、撤销与审计记录。
- 全新响应式工作台：覆盖记忆、实体、图谱、请求、Webhook、导出、设置与已连接应用。
- 工作台同源 API 传输，浏览器不再依赖单独暴露的 API 地址。
- 面向本地运维者的 Docker Compose 部署和升级文档。

## 本地部署

在全新代码检出中初始化密钥并启动自托管服务：

```bash
./scripts/init.sh
cd server
docker compose up -d
```

Windows PowerShell：

```powershell
.\scripts\init.ps1
Set-Location server
docker compose up -d
```

默认本地工作台地址为 `http://localhost:3000`。将任何服务暴露到公网前，请阅读
[运维指南](OPERATIONS.zh-CN.md)和[故障排查](TROUBLESHOOTING.zh-CN.md)。

## 发布边界

公开官网由独立项目维护和部署。官网源码、素材与部署配置明确不进入 YiQiao 0.2.0
开源仓库及发布产物。
