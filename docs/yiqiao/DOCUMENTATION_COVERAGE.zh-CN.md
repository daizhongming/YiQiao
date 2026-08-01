# 文档语言覆盖清单

**简体中文** | [English](DOCUMENTATION_COVERAGE.md)

最后审查：2026-07-27

本清单是 YiQiao 面向公开用户的英文与简体中文文档的权威范围。英文原文仍是规范来源。
只有同一提交上的 `make docs-check` 成功退出时，表中的 `pass` 校验值才有效。

类型与策略：

- `markdown`：按下表要求提供双向语言链接，并保持 shell 类围栏代码块完全一致。
- `legal`：英文法律载荷保持字节稳定；中文参考译文必须链接英文原文并带有非官方译文声明。
- `issue-form`：GitHub Issue Form 中英文表单必须保留必填字段，并能相互切换。
- `env`：可执行的环境变量赋值必须完全一致，只翻译注释。

<!-- docs-localization:pairs:start -->
| 英文原文 | 简体中文 | 类型 | 双向链接 | Shell 代码块 | 翻译状态 | 校验状态 |
| --- | --- | --- | --- | --- | --- | --- |
| `README.md` | `README.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `CONTRIBUTING.md` | `CONTRIBUTING.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `SECURITY.md` | `SECURITY.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `CODE_OF_CONDUCT.md` | `CODE_OF_CONDUCT.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `OPEN_SOURCE_READINESS.md` | `OPEN_SOURCE_READINESS.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `BRANDING_EXCEPTIONS.md` | `BRANDING_EXCEPTIONS.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `MODIFICATIONS.md` | `MODIFICATIONS.zh-CN.md` | `legal` | `legal-exception` | `not-applicable` | `complete` | `pass` |
| `THIRD_PARTY_NOTICES.md` | `THIRD_PARTY_NOTICES.zh-CN.md` | `legal` | `legal-exception` | `not-applicable` | `complete` | `pass` |
| `LICENSE` | `LICENSE.zh-CN.md` | `legal` | `legal-exception` | `not-applicable` | `complete` | `pass` |
| `NOTICE` | `NOTICE.zh-CN.md` | `legal` | `legal-exception` | `not-applicable` | `complete` | `pass` |
| `docs/yiqiao/README.md` | `docs/yiqiao/README.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/LEGAL.md` | `docs/yiqiao/LEGAL.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/MIGRATION.md` | `docs/yiqiao/MIGRATION.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/OPERATIONS.md` | `docs/yiqiao/OPERATIONS.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/RELEASE_0.2.0.md` | `docs/yiqiao/RELEASE_0.2.0.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/RELEASE_0.2.1.md` | `docs/yiqiao/RELEASE_0.2.1.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/SECURITY_AUDIT.md` | `docs/yiqiao/SECURITY_AUDIT.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/TROUBLESHOOTING.md` | `docs/yiqiao/TROUBLESHOOTING.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/DOCUMENTATION_COVERAGE.md` | `docs/yiqiao/DOCUMENTATION_COVERAGE.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `server/README.md` | `server/README.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `server/dashboard/public/fonts/FONT_LICENSES.md` | `server/dashboard/public/fonts/FONT_LICENSES.zh-CN.md` | `legal` | `legal-exception` | `not-applicable` | `complete` | `pass` |
| `.github/PULL_REQUEST_TEMPLATE.md` | `.github/PULL_REQUEST_TEMPLATE.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `.github/ISSUE_TEMPLATE/bug_report.yml` | `.github/ISSUE_TEMPLATE/bug_report.zh-CN.yml` | `issue-form` | `required` | `not-applicable` | `complete` | `pass` |
| `.github/ISSUE_TEMPLATE/documentation_issue.yml` | `.github/ISSUE_TEMPLATE/documentation_issue.zh-CN.yml` | `issue-form` | `required` | `not-applicable` | `complete` | `pass` |
| `.github/ISSUE_TEMPLATE/feature_request.yml` | `.github/ISSUE_TEMPLATE/feature_request.zh-CN.yml` | `issue-form` | `required` | `not-applicable` | `complete` | `pass` |
| `server/.env.example` | `server/.env.example.zh-CN` | `env` | `required` | `not-applicable` | `complete` | `pass` |
<!-- docs-localization:pairs:end -->

以下路径有意不采用文档配对规则。检查器会验证精确路径仍然存在；模式则定义长期有效的扫描边界。

<!-- docs-localization:exclusions:start -->
| 路径或模式 | 不翻译原因 |
| --- | --- |
| `.github/ISSUE_TEMPLATE/config.yml` | GitHub 只支持一个保留的选择器配置；其中的可见文字已采用中英双语。 |
| `server/dashboard/public/fonts/OFL-1.1.txt` | 未修改的第三方 SIL OFL 文本必须保持原文。 |
| `server/dashboard/.env.example` | Dashboard 构建时模板；运维配置已记录在成对的服务端环境变量模板中。 |
| `server/requirements.txt` | 供工具读取的依赖清单。 |
| `server/dashboard/pnpm-lock.yaml` | 生成的依赖锁文件。 |
| `.github/workflows/**` | CI 配置，不是用户文档。 |
| `scripts/**` | 可执行维护源码，不是用户文档。 |
| `tests/**` | 测试源码和固件，不是用户文档。 |
| `mem0/**` | 内部兼容实现源码。 |
| `yiqiao/**` | Python 实现源码。 |
| `server/**/*.py` | 服务端实现源码。 |
| `server/dashboard/src/**` | Dashboard 实现和界面源码。 |
| `server/docker-compose*.yaml` | 供工具读取的部署配置。 |
| `server/**/Dockerfile*` | 容器构建配置。 |
| `Makefile` | 构建自动化。 |
| `pyproject.toml` | Python 构建和工具配置。 |
| `**/node_modules/**` | 已安装依赖。 |
| `**/.next/**` | 生成的 Dashboard 构建产物。 |
| `**/build/**` | 生成的构建产物。 |
| `**/dist/**` | 生成的发行产物。 |
| `**/coverage/**` | 生成的覆盖率产物。 |
| `**/htmlcov/**` | 生成的 HTML 覆盖率产物。 |
| `**/history/**` | 运行历史和已导入的用户数据。 |
| `**/logs/**` | 运行日志。 |
| `**/backups/**` | 运维备份。 |
| `**/artifacts/**` | 生成的测试或发行产物。 |
| `**/playwright-report/**` | 生成的浏览器测试报告。 |
| `**/test-results/**` | 生成的测试输出。 |
| `**/__pycache__/**` | 生成的 Python 字节码缓存。 |
<!-- docs-localization:exclusions:end -->

提交文档改动前请运行 `make docs-check`。检查器以本清单为依据，验证每个英文原文和中文版本，
发现未登记的英文 Markdown 与 MDX 文件，并拒绝过期记录。
