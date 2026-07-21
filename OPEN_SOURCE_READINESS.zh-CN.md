# YiQiao 开源发布就绪说明

**简体中文** | [English](OPEN_SOURCE_READINESS.md)

最后更新：2026-07-20

本文定义 YiQiao 的发布门槛，并记录首次公开源代码发布的设计。对应准确提交的 GitHub Actions
结果是权威的远程证据；仅有本地结果不能替代它们。

## 发布范围

公开仓库包含 YiQiao 记忆 API、运维控制台、Python 兼容核心、数据库迁移、Docker Compose
部署、初始化与维护脚本、测试和项目文档。仓库不得包含本地环境文件、运行历史、日志、浏览器
输出、数据库转储、备份、缓存、凭据或用户数据。

YiQiao 是 Mem0 开源项目的独立衍生作品，不是 Mem0 官方产品，也不主张对继承代码拥有排他
所有权。发布内容会保留 Apache License 2.0、上游版权声明、上游源代码引用、第三方声明，
以及 `MODIFICATIONS.md` 中经过审计的上游文件修改清单。

## 公开历史

YiQiao 首个版本会以源代码快照的形式，添加在目标仓库原有的占位提交之上。该版本不会从上游
历史或审查分支历史执行合并、变基或拣选提交。这样既能让 YiQiao 的公开历史聚焦于本产品，
又能由 `NOTICE` 和 `MODIFICATIONS.md` 保留代码本身所需的来源信息。

公开版本不包含 `.gitleaksignore`。Gitleaks 必须对完整公开历史和当前检出的代码树均报告
零项发现。`.gitleaks.toml` 中的严格规则只接受已提交环境变量模板里相邻且值为空的服务商
密钥占位符。

## 必须通过的检查

| 范围 | 必需证据 |
| --- | --- |
| 源代码身份 | 只包含来自准确 Git 索引的文件；不包含复制的发布、冒烟测试、备份或运行目录 |
| 许可证 | Apache 修改声明审计通过，并且所有法律文件均存在于源代码、wheel 和镜像中 |
| 密钥 | Gitleaks 对当前代码树和完整历史的扫描均为零项发现，且不使用指纹例外 |
| Python | 格式、代码检查、编译、核心测试、服务商隔离测试和发布法律测试全部通过 |
| 控制台 | 冻结依赖安装、Prettier、零警告 ESLint、TypeScript、单元测试和生产构建全部通过 |
| 依赖 | Python 和控制台依赖检查没有报告已知且需要处理的漏洞 |
| Compose | 基础、源码构建、生产、发布和端到端配置均能成功解析 |
| 镜像 | API 和控制台可为受支持的架构完成构建；发布镜像包含来源证明和 SBOM |
| 运行时 | 初始化、健康检查、管理员设置、API 密钥、记忆写入与检索、重启和持久化全部通过 |
| 用户体验 | 桌面端和移动端的登录、请求、记忆及关系图视图无控制台错误或内容溢出 |
| 文档 | 内部链接、快速开始中的原样命令、品牌、安全、运维和回滚说明全部通过审查 |

仓库的四个工作流有意保持独立：

- `YiQiao CI` 验证源代码、Python、控制台、Compose 和打包。
- `YiQiao Security` 扫描密钥和依赖。
- `YiQiao Full Stack` 验证从干净源代码构建的完整部署。
- `YiQiao Images` 在推送时验证多架构镜像，且仅在默认分支显式调度并设置
  `publish=true` 后才发布镜像。

## 可复现的本地检查

推送前，请运行与改动相关的检查：

```bash
python scripts/audit_modification_notices.py --fetch-base
python -m pytest -q tests/test_release_legal_payload.py
python -m pytest -q tests/
ruff format --check .
ruff check .
gitleaks dir --redact=100 --no-banner .
gitleaks git --redact=100 --no-banner .
```

```bash
cd server/dashboard
pnpm install --frozen-lockfile
pnpm run format:check
pnpm run lint
pnpm run typecheck
pnpm run test:unit
pnpm run build
pnpm audit --audit-level high
```

```bash
cd server
docker compose config --quiet
docker compose -f docker-compose.yaml -f docker-compose.build.yaml config --quiet
docker compose -f docker-compose.yaml -f docker-compose.production.yaml config --quiet
```

使用 `python scripts/full_stack_smoke.py` 执行隔离的源码构建运行时测试。该脚本会创建唯一的
Compose 项目，不得复用生产数据。

## 发布检查清单

1. 要求工作树为空，并确认已提交代码树与经过审查的索引导出内容一致。
2. 使用常规的快进更新将快照推送到 `main`。发布时绝不能强制推送。
3. 要求全部四个工作流在准确的 `main` 提交上成功运行。
4. 在受保护的 `main` 上强制执行必需检查，并禁止强制推送和分支删除；随后在该准确提交上
   创建 `v0.1.0`，并确认标签解析到已审查的 `main` SHA。
5. 在 `main` 上调度 `YiQiao Images`，设置 `publish=true` 和 `version=v0.1.0`。如果版本标签
   不存在或指向其他提交，工作流必须拒绝发布。
6. 确认两个 GHCR 软件包均为公开状态，且匿名访问能够解析 `latest`、`v0.1.0` 和完整提交
   SHA 标签；记录每个镜像不可变的多平台摘要。
7. 为 `v0.1.0` 创建中英双语 GitHub Release，其中包含准确的源码 SHA、镜像标签与摘要、
   安全和升级指引、Mem0 归属说明以及 Apache-2.0 许可信息。
8. 从干净检出中，按照 README 原样执行克隆、初始化程序、默认 Compose 启动、健康检查、
   首次设置、记忆写入和检索流程。
9. 删除过时的公开审查引用，确保无法通过 YiQiao 分支访问上游历史，然后重新执行针对所有
   引用的密钥与路径审计。
10. 在公告发布或升级部署之前，核对 Git 标签、GitHub Release、受保护分支、工作流 SHA 和
    全部三个镜像标签完全一致。

## 剩余风险

- 可选的服务商和向量存储适配器依赖外部服务、许可证、凭据、速率限制、隐私条款，以及由
  运维人员决定的数据驻留方案。
- 默认 Compose 部署为单主机部署，不具备高可用性。
- Neo4j Community 不具备企业版集群和在线备份功能；创建一致的图数据备份需要维护窗口或
  存储快照。
- 在控制台中配置的服务商密钥存储于 PostgreSQL。运维人员必须保护数据库备份、管理员会话
  和主机访问权限。
- `0.1.x` 是首个开源版本系列；运维人员应在每次升级前检查 API、数据模式、服务商和镜像兼容性。

## 回滚

请使用经过审查的常规提交还原源代码更改，不要重置或改写 `main`。对于已部署的系统，
应先记录当前镜像摘要和数据状态；仅当旧镜像与迁移后的数据模式兼容时，才能使用该镜像。
如迁移无法向后兼容，则需要部署隔离的替代实例，并恢复对应的 PostgreSQL、Neo4j、历史记录
和加密配置备份。完整流程见 `docs/yiqiao/OPERATIONS.md`。
