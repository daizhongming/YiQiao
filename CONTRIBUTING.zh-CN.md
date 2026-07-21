# 参与 YiQiao 开发
> **Modification notice:** This file was modified in 2026 by YiQiao contributors. See NOTICE.

**简体中文** | [English](CONTRIBUTING.md)

YiQiao 欢迎范围清晰的缺陷修复、安全加固、文档、测试，以及能够改善自托管 API、仪表盘、记忆核心、数据库迁移或运维体验的功能。

## 开始之前

请先搜索[现有 Issue](https://github.com/daizhongming/YiQiao/issues)。涉及大范围行为、数据库结构、依赖或架构的变更，应在编码前创建 Issue，以便先确认范围和迁移路径。安全问题必须按照[安全政策](SECURITY.zh-CN.md)私密报告，不得使用公开 Issue。

参与项目即表示你同意遵守[行为准则](CODE_OF_CONDUCT.zh-CN.md)。除非另有明确说明，所有贡献均按仓库的 Apache License 2.0 提交。

## 仓库范围

| 区域 | 路径 | 主要检查 |
| --- | --- | --- |
| Python 公开入口 | `yiqiao/` | Ruff、编译、pytest |
| 记忆核心 | 内部兼容实现 | Ruff、编译、pytest |
| API 与迁移 | `server/`、`server/alembic/` | Ruff、编译、pytest、Compose |
| 仪表盘 | `server/dashboard/` | Prettier、TypeScript、Next.js 构建 |
| 部署 | `server/docker-compose*.yaml`、`scripts/` | Compose 配置、干净构建、冒烟测试 |
| 发布文档 | 根目录 Markdown、`docs/yiqiao/` | 链接、命令、品牌与许可审查 |

Python 发行包名为 `yiqiao-memory`，公开代码应从 `yiqiao` 导入。内部兼容标识只用于保障已有实现和数据可迁移；不要在新接口、示例或产品文案中增加旧标识。需要改变兼容层时，必须同时更新[品牌兼容登记](BRANDING_EXCEPTIONS.zh-CN.md)并提供迁移方案。

## 开发环境

初始化本地 Compose 配置：

```bash
git clone https://github.com/daizhongming/YiQiao.git
cd YiQiao
./scripts/init.sh
```

Windows PowerShell 使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1
```

初始化脚本不会覆盖已有的 `server/.env`。

Python 开发环境：

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test,dev]"
python -m pip install -r server/requirements.txt
```

仪表盘开发环境：

```bash
cd server/dashboard
corepack enable
pnpm install --frozen-lockfile
```

## 必需检查

至少运行与你修改区域对应的检查。完整 Python 快速检查：

```bash
make format-check
make lint
make test
python scripts/audit_modification_notices.py --fetch-base
```

Provider adapter 变更还需安装可选依赖并运行独立测试集：

```bash
python -m pip install -e ".[test,vector-stores,llms,extras,nlp]"
python -m pytest -q \
  tests/embeddings tests/llms tests/rerankers tests/vector_stores
```

仪表盘变更：

```bash
cd server/dashboard
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm build
```

部署变更：

```bash
cd server
docker compose config --quiet
docker compose -f docker-compose.yaml -f docker-compose.production.yaml config --quiet
docker compose -f docker-compose.yaml -f docker-compose.build.yaml build
```

测试不得读取开发者的 `server/.env`，不得写入真实 YiQiao 数据卷，不得在没有明确集成测试标记的情况下调用付费模型服务，也不得依赖之前运行过的本地容器。每次数据库结构变更都必须添加迁移，并同时测试全新数据库与升级路径。

## Pull Request

1. 从当前默认分支或指定发布分支创建工作分支。
2. 每个 Pull Request 只包含一个逻辑变更。
3. 为行为变更补充回归测试以及用户或运维文档。
4. 使用 `fix:`、`feat:`、`docs:` 或 `test:` 等 Conventional Commit 前缀。
5. 适用时通过 `Closes #<编号>` 关联 Issue。
6. 填写 Pull Request 模板，并列出实际执行过的验证命令。
7. 明确说明迁移、兼容性、新增网络访问、密钥、遥测、依赖和许可证影响。

不要提交 `.env`、凭据、数据库内容、运行历史、日志、包含用户数据的截图、Playwright 输出、构建产物或本地备份。推送前应运行密钥扫描。

## 文档与品牌

产品、界面、镜像、服务、示例和所有新增公开接口统一使用 **YiQiao**。上游法律归属仅保留在 `NOTICE`、`THIRD_PARTY_NOTICES.md` 和明确标注的来源说明中。不得添加上游托管产品、支持渠道、社交账号或发布流水线链接。

新增或更新依赖时：

- 优先选择相关组件已经采用且仍在维护的依赖。
- 按现有锁文件策略统一固定或限制版本。
- 审查已知漏洞和传递依赖变化。
- 若依赖进入发布产物，应在 `THIRD_PARTY_NOTICES.md` 记录许可证与分发义务。

## 审查说明

维护者可能要求拆分提交、补充测试、提供迁移或回滚方案、调整安全实现，或澄清许可证。获得批准不代表会立即发布；只有仓库的全部质量门禁通过后，维护者才会创建发布版本。
