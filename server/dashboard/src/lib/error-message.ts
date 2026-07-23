// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import axios from "axios";

const LANGUAGE_STORAGE_KEY = "yiqiao_language";

const ERROR_TRANSLATIONS: Record<string, string> = {
  "Something went wrong": "发生了错误",
  "Request failed": "请求失败",
  Unauthorized: "未授权",
  "Network Error": "网络连接失败",
  "YiQiao service is temporarily unavailable.":
    "忆桥服务暂时不可用，请稍后重试。",
  "Field required": "此字段为必填项",
  "JWT_SECRET is not configured.": "JWT_SECRET 尚未配置。",
  "Refresh token is no longer valid.": "刷新令牌已失效，请重新登录。",
  "Invalid or expired token.": "令牌无效或已过期，请重新登录。",
  "Invalid token type.": "令牌类型无效。",
  "User not found.": "未找到用户。",
  "API key owner not found.": "未找到 API 密钥所有者。",
  "Invalid API key.": "API 密钥无效。",
  "Project access denied.": "无权访问该项目。",
  "Organization access denied.": "无权访问该组织。",
  "Usage subject access denied.": "无权查看该用量对象。",
  "Authentication required.": "需要登录后才能继续。",
  "Authentication required. Provide a Bearer token or X-API-Key header.":
    "需要身份验证。请提供 Bearer 令牌或 X-API-Key 请求头。",
  "Admin role required.": "此操作需要管理员权限。",
  "Registration is open only for invited workspace members.":
    "仅受邀的工作区成员可以注册。",
  "Email is already in use.": "该邮箱已被使用。",
  "Invalid email or password.": "邮箱或密码错误。",
  "Current password is incorrect.": "当前密码不正确。",
  "Cannot create an API key for another project.":
    "不能为其他项目创建 API 密钥。",
  "API key not found.": "未找到 API 密钥。",
  "API key is already revoked.": "API 密钥已被撤销。",
  "Member role must be READER, EDITOR, or OWNER.":
    "成员角色必须是只读者、编辑者或所有者。",
  "Member role must be READER or OWNER.": "成员角色必须是只读者或所有者。",
  "Member status must be active or invited.": "成员状态必须是已激活或已邀请。",
  "Member not found.": "未找到成员。",
  "Project member not found.": "未找到项目成员。",
  "Project not found.": "未找到项目。",
  "Organization not found.": "未找到组织。",
  "You cannot remove your own active workspace membership.":
    "不能移除自己当前有效的工作区成员身份。",
  "You cannot remove your own active project membership.":
    "不能移除自己当前有效的项目成员身份。",
  "Default organization cannot be deleted.": "不能删除默认组织。",
  "Default project cannot be deleted.": "不能删除默认项目。",
  "Each category requires a name.": "每个分类都必须填写名称。",
  "A project can have at most 100 categories.":
    "每个项目最多可设置 100 个分类。",
  "email is required.": "邮箱为必填项。",
  "Unsupported quota scope.": "不支持该配额作用域。",
  "Unsupported quota metric.": "不支持该配额指标。",
  "Unsupported quota period.": "不支持该配额周期。",
  "Stored memories only supports the total period.":
    "已存储记忆仅支持总量周期。",
  "Request quotas require a time period.": "请求配额必须设置时间周期。",
  "Storage limits are supported for organizations and projects only.":
    "存储限制仅支持组织和项目作用域。",
  "Quota limit must be greater than zero.": "配额上限必须大于 0。",
  "Unsupported quota mode.": "不支持该配额模式。",
  "Warning threshold must be between 0 and 1.": "预警阈值必须在 0 到 1 之间。",
  "Quota scope ID is required.": "配额作用域 ID 为必填项。",
  "Select the project before managing its limits.":
    "请先选择项目，再管理其限制。",
  "Select the project before viewing its usage.":
    "请先选择项目，再查看其用量。",
  "Duplicate metric and period in quota policies.":
    "配额策略中存在重复的指标和周期。",
  "Webhook URL must be http or https.": "Webhook URL 必须使用 http 或 https。",
  "Select at least one webhook event.": "请至少选择一个 Webhook 事件。",
  "Webhook not found.": "未找到 Webhook。",
  "Memory not found.": "未找到记忆。",
  "Entity not found.": "未找到实体。",
  "Memory export not found.": "未找到记忆导出任务。",
  "Memory import not found.": "未找到记忆导入任务。",
  "Select at least one file to import.": "请至少选择一个要导入的文件。",
  "Import options must be valid JSON.": "导入选项必须是有效的 JSON。",
  "Entity IDs cannot be empty.": "实体 ID 不能为空。",
  "Each entity type can be configured only once.": "每种实体类型只能配置一次。",
  "No supported files were selected. Use Markdown, text, JSON, JSONL, ZIP, or TAR files.":
    "没有选中支持的文件。请选择 Markdown、文本、JSON、JSONL、ZIP 或 TAR 文件。",
  "No supported chat history files were found.": "未找到支持的聊天记录文件。",
  "The selected files did not contain any importable messages.":
    "所选文件中没有可导入的消息。",
  "memory_id is required.": "memory_id 为必填项。",
  "At least one identifier is required.": "请至少提供一个标识符。",
  "At least one identifier (user_id, agent_id, app_id, run_id) is required.":
    "请至少提供 user_id、agent_id、app_id 或 run_id 中的一项。",
  "entity_type must be user, agent, app, or run.":
    "entity_type 必须是 user、agent、app 或 run。",
  "The LLM returned an incomplete instruction response. Please retry.":
    "大语言模型返回的指令不完整，请重试。",
  "The LLM returned incomplete category JSON. Please retry.":
    "大语言模型返回的分类 JSON 不完整，请重试。",
  "The LLM did not return any usable categories. Please retry.":
    "大语言模型未返回可用分类，请重试。",
  "Provider rejected the request (authentication). Check your LLM provider API key on the Configuration page.":
    "模型提供商拒绝了身份验证。请在配置页检查 API 密钥。",
  "Provider rate limit hit. Retry shortly.":
    "已达到模型提供商的速率限制，请稍后重试。",
  "Provider timed out. Retry shortly.": "模型提供商响应超时，请稍后重试。",
  "Provider is unreachable or returned a server error.":
    "无法连接模型提供商，或提供商返回了服务器错误。",
  "Provider rejected the request as malformed.": "模型提供商认为请求格式无效。",
  "The memory database is unreachable.": "无法连接记忆数据库。",
  "The vector store is unreachable or returned an error.":
    "无法连接向量存储，或向量存储返回了错误。",
  "Upstream provider error.": "上游模型提供商发生错误。",
};

function prefersChinese() {
  if (typeof window === "undefined") return false;
  return localStorage.getItem(LANGUAGE_STORAGE_KEY) !== "en";
}

export function translateKnownErrorMessage(message: string): string | null {
  const normalized = message.trim();
  const exact = ERROR_TRANSLATIONS[normalized];
  if (exact) return exact;

  let match = normalized.match(
    /^Password must be at least (\d+) characters\.?$/i,
  );
  if (match) return `密码至少需要 ${match[1]} 个字符。`;

  match = normalized.match(/^Unsupported webhook event:\s*(.+)$/i);
  if (match) return `不支持的 Webhook 事件：${match[1]}`;

  match = normalized.match(
    /^Embedding dimension mismatch: configured (\d+), provider returned (\d+)\.?$/i,
  );
  if (match) {
    return `嵌入维度不匹配：配置为 ${match[1]}，提供商返回 ${match[2]}。`;
  }

  match = normalized.match(/^Request failed with status code (\d+)$/i);
  if (match) return `请求失败（HTTP ${match[1]}）`;

  if (/^timeout of \d+ms exceeded$/i.test(normalized))
    return "请求超时，请重试。";

  match = normalized.match(/^(llm|embedder|reranker) test failed:\s*(.+)$/i);
  if (match) {
    const kind =
      match[1].toLowerCase() === "llm"
        ? "大语言模型"
        : match[1].toLowerCase() === "embedder"
          ? "嵌入模型"
          : "重排模型";
    return `${kind}测试失败：${translateKnownErrorMessage(match[2]) ?? match[2]}`;
  }

  return null;
}

function formatStructuredDetail(detail: Record<string, unknown>) {
  const code = typeof detail.code === "string" ? detail.code : "";
  const metric = typeof detail.metric === "string" ? detail.metric : "";
  const limit = detail.limit;
  const metricLabels: Record<string, string> = {
    api_requests: "API 请求",
    memory_writes: "记忆写入",
    memory_searches: "记忆搜索",
    stored_memories: "已存储记忆",
  };

  if (code === "quota_exceeded" || code === "storage_quota_exceeded") {
    if (prefersChinese()) {
      const label = metricLabels[metric] ?? metric ?? "当前";
      return `${label}已达到配额上限${limit != null ? `（${String(limit)}）` : ""}。`;
    }
    return `Quota exceeded${metric ? ` for ${metric}` : ""}${limit != null ? ` (limit: ${String(limit)})` : ""}.`;
  }

  if (typeof detail.message === "string") return detail.message;
  if (typeof detail.error === "string") return detail.error;
  return null;
}

function localize(message: string) {
  if (!prefersChinese()) return message;
  return translateKnownErrorMessage(message) ?? message;
}

export function getErrorMessage(
  err: unknown,
  fallback = "Something went wrong",
): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === "string") return localize(detail);
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0];
      if (typeof first === "string") return localize(first);
      if (first?.msg) return localize(String(first.msg));
    }
    if (detail && typeof detail === "object") {
      const structured = formatStructuredDetail(
        detail as Record<string, unknown>,
      );
      if (structured) return localize(structured);
    }
    if (err.message) return localize(err.message);
  }
  if (err instanceof Error && err.message) return localize(err.message);
  if (typeof err === "string") return localize(err);
  return localize(fallback);
}
