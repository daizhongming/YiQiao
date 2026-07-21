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
