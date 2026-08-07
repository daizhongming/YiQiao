// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";
import { translateKnownErrorMessage } from "@/lib/error-message";
import { LANGUAGE_PREFERENCE_KEY } from "@/lib/language-preference";
import type { Language } from "@/lib/language-preference";

export type { Language } from "@/lib/language-preference";

const translations: Record<string, string> = {
  SETUP: "设置",
  ACTIVITY: "活动",
  ADVANCED: "高级",
  ACCOUNT: "账号",
  Install: "安装",
  Playground: "调试台",
  "Playground Settings": "调试台设置",
  Dashboard: "仪表盘",
  Requests: "请求",
  Retrieval: "检索",
  Other: "其他",
  Memories: "记忆",
  Entities: "实体",
  Graph: "图谱",
  Categories: "分类",
  Webhooks: "Webhook",
  "Memory Exports": "记忆导出",
  "API Keys": "API 密钥",
  Configuration: "配置",
  Settings: "设置",
  Usage: "用量",
  Documentation: "文档",
  "Log out": "退出登录",
  "Expand sidebar": "展开侧边栏",
  "Collapse sidebar": "收起侧边栏",
  Navigation: "导航",
  "Open navigation": "打开导航",
  "Switch to English": "切换到英文",
  "Switch to Chinese": "切换到中文",
  "Default organization": "默认组织",

  "Sign in to YiQiao": "登录 YiQiao",
  "Create invited account": "创建受邀账号",
  Name: "姓名",
  "Your name": "你的姓名",
  Email: "邮箱",
  Password: "密码",
  "Create account": "创建账号",
  "Sign in": "登录",
  "Creating...": "创建中...",
  "Signing in...": "登录中...",
  "Already have an account?": "已有账号？",
  "Have an invite?": "有邀请？",
  "Forgot password?": "忘记密码？",
  "Reset your admin password": "重置管理员密码",
  "Run this command on the server host. It overwrites the existing password; anyone already signed in stays signed in until their session expires.":
    "在服务器主机上运行此命令。它会覆盖现有密码；已经登录的用户会保持登录直到会话过期。",
  "Enter a valid email address.": "请输入有效邮箱地址。",
  "Enter your name.": "请输入你的姓名。",
  "Login failed": "登录失败",
  "YiQiao - Log in": "YiQiao - 登录",
  "Log in to YiQiao": "登录 YiQiao",

  "Admin Account": "管理员账号",
  Providers: "提供商",
  "API Key": "API 密钥",
  "Use Case": "使用场景",
  "Quick Test": "快速测试",
  "Create your admin account": "创建管理员账号",
  "Review provider configuration": "检查提供商配置",
  "Your API key": "你的 API 密钥",
  "Tell us your use case": "告诉我们你的使用场景",
  "Test your setup": "测试你的配置",
  "Confirm Password": "确认密码",
  "Create Admin Account": "创建管理员账号",
  "Checking server configuration...": "正在检查服务器配置...",
  "No LLM provider API key is configured on the server. Paste one below to continue - it will be saved to the server and used for all memory operations.":
    "服务器尚未配置 LLM 提供商 API 密钥。请在下方粘贴一个继续，它会保存到服务器并用于所有记忆操作。",
  "No LLM provider API key is configured on the server. Paste one below to continue — it will be saved to the server and used for all memory operations.":
    "服务器尚未配置 LLM 提供商 API 密钥。请在下方粘贴一个继续，它会保存到服务器并用于所有记忆操作。",
  "LLM Provider": "LLM 提供商",
  "LLM API Key": "LLM API 密钥",
  "Embedder Provider": "嵌入模型提供商",
  Model: "模型",
  "Select provider": "选择提供商",
  "Leave blank to keep existing key": "留空以保留现有密钥",
  "Min 8 characters": "至少 8 个字符",
  "sk-...": "sk-...",
  "admin@company.com": "admin@company.com",
  "Also used for the embedder when it shares the same provider.":
    "当嵌入模型使用相同提供商时，也会使用这个密钥。",
  "Need another provider? Install its Python package and rebuild the image. See":
    "需要其他提供商？安装对应 Python 包并重建镜像。查看",
  "supported providers": "支持的提供商",
  "Failed to save instructions": "保存指令失败",
  "NEXT_PUBLIC_API_URL is not set. Set it in .env and restart before running this test.":
    "NEXT_PUBLIC_API_URL 未设置。请在 .env 中配置并重启后再运行测试。",
  "Provider credentials or model wrong? Fix them in":
    "提供商凭据或模型不正确？请到",
  "and run the test again.": "修复后再运行测试。",
  "Save & Continue": "保存并继续",
  "Saving...": "保存中...",
  "Label for this key": "密钥标签",
  "My First Key": "我的第一个密钥",
  "Generate API Key": "生成 API 密钥",
  "Generating...": "生成中...",
  "Save this key. You will not see it again.":
    "请保存这个密钥。它不会再次显示。",
  Continue: "继续",
  "Describe your use case": "描述你的使用场景",
  "e.g. A personal assistant that remembers my preferences":
    "例如：一个能记住我偏好的个人助手",
  "Personal assistant": "个人助手",
  "Coding agent": "编程智能体",
  "Customer support": "客户支持",
  Research: "研究",
  "Therapy / journaling": "疗愈 / 日记",
  "We'll generate custom instructions that tell the memory extractor which facts to prioritize for your use case.":
    "我们会生成自定义指令，告诉记忆提取器在你的场景里优先关注哪些事实。",
  Skip: "跳过",
  "Generate instructions": "生成指令",
  "Generating instructions...": "正在生成指令...",
  "Generated instructions": "已生成指令",
  Testing: "测试中",
  "Testing...": "测试中...",
  "Run Test": "运行测试",
  "Memory created successfully": "记忆创建成功",
  "Go to Dashboard": "进入仪表盘",
  "Setup | YiQiao": "设置 | YiQiao",
  "Set up your YiQiao instance": "配置你的 YiQiao 实例",

  Memory: "记忆",
  Content: "内容",
  User: "用户",
  Agent: "智能体",
  App: "应用",
  Run: "运行",
  Created: "创建时间",
  Updated: "更新时间",
  Project: "项目",
  Metadata: "元数据",
  History: "历史",
  "Memory Detail": "记忆详情",
  "View memory content and metadata": "查看记忆内容和元数据",
  "No history entries.": "没有历史记录。",
  "Invalid metadata JSON": "无效的元数据 JSON",
  "Metadata must be a JSON object.": "元数据必须是 JSON 对象。",
  "Failed to delete memory": "删除记忆失败",
  "Failed to update memory": "更新记忆失败",
  "Save changes": "保存更改",
  "Delete memory": "删除记忆",
  "Delete Memory": "删除记忆",
  "Delete memory?": "删除记忆？",
  "This memory will be permanently removed. This cannot be undone.":
    "这条记忆会被永久移除。此操作无法撤销。",
  "No memories yet": "暂无记忆",
  "Create your first memory by sending a POST /memories request.":
    "发送 POST /memories 请求创建第一条记忆。",
  "1,000+ memories stored. Categories can help organize them.":
    "已存储 1,000+ 条记忆。分类可以帮助整理它们。",
  "Open categories": "打开分类",
  "REST API reference": "REST API 参考",
  of: "/",
  Apply: "应用",
  Previous: "上一页",
  Next: "下一页",

  Type: "类型",
  ID: "ID",
  "Last Active": "最近活跃",
  "Entity Detail": "实体详情",
  "Related memories": "相关记忆",
  "No memories found for this entity.": "未找到该实体的记忆。",
  "Delete entity": "删除实体",
  "Failed to delete entity": "删除实体失败",
  "Failed to load entity memories": "加载实体记忆失败",
  "Last active": "最近活跃",
  "View entity fields and related memories": "查看实体字段和相关记忆",
  "All memories associated with this entity will be permanently removed. This cannot be undone.":
    "与该实体关联的所有记忆都会被永久移除。此操作无法撤销。",
  "No entities yet": "暂无实体",
  "Entities appear once memories are stored with a user_id, agent_id, or run_id.":
    "当记忆带有 user_id、agent_id 或 run_id 存储后，实体会显示在这里。",

  "Sync Neo4j": "同步 Neo4j",
  "Graph synced": "图谱已同步",
  "Neo4j is not enabled": "Neo4j 未启用",
  "Entity-memory links stored in Neo4j.": "实体-记忆关联存储在 Neo4j 中。",
  "Entity links, scope links, and related memories stored in Neo4j.":
    "实体链接、作用域链接和相关记忆存储在 Neo4j 中。",
  Relationships: "关系",
  "Top Graph Entities": "图谱实体排行",
  "No graph entities": "暂无图谱实体",
  "Failed to load graph entities": "加载图谱实体失败",
  READER: "只读者",
  OWNER: "所有者",

  "Custom Categories": "自定义分类",
  "Add Category": "添加分类",
  "Category Name": "分类名称",
  Category: "分类",
  Description: "描述",
  Uncategorized: "未分类",
  All: "全部",
  "All categories": "全部分类",
  "Selected memory category": "选中记忆的分类",
  "Organize memories with project-level categories.":
    "使用项目级分类来组织记忆。",
  "Save category": "保存分类",
  "Failed to save category": "保存分类失败",
  "e.g. Preferences": "例如：偏好",
  "Categories appear once memories are stored.": "存储记忆后会显示分类。",
  "No categories yet": "暂无分类",

  Webhook: "Webhook",
  "Webhook Name": "Webhook 名称",
  "Webhook created": "Webhook 已创建",
  "Webhook deleted": "Webhook 已删除",
  "Create Webhook": "创建 Webhook",
  "Test Webhook": "测试 Webhook",
  Enabled: "已启用",
  Events: "事件",
  URL: "URL",
  "Signing secret": "签名密钥",
  "Copy signing secret": "复制签名密钥",
  "Delete webhook": "删除 Webhook",

  Status: "状态",
  Disabled: "已禁用",
  Method: "方法",
  Path: "路径",
  Latency: "延迟",
  "Auth Type": "认证类型",
  "Admin Key": "管理员密钥",
  "Request Details": "请求详情",
  "No requests yet": "暂无请求",
  "Request Detail": "请求详情",
  "View request log fields": "查看请求日志字段",
  Time: "时间",
  Auth: "认证",
  "Total Requests": "请求总数",
  "Success Rate": "成功率",
  "Recent request logs from your self-hosted instance.":
    "来自当前自托管实例的最近请求日志。",
  "Last updated": "最后更新",
  Refresh: "刷新",
  "Any method": "任意方法",
  "Path contains": "路径包含",
  "No request logs yet": "暂无请求日志",
  "Requests will appear here once your instance receives traffic.":
    "实例收到流量后，请求会显示在这里。",
  "Dashboard | YiQiao": "仪表盘 | YiQiao",
  "YiQiao Dashboard": "YiQiao 仪表盘",

  "Create Export": "创建导出",
  "Export Details": "导出详情",
  "Memory Export": "记忆导出",
  Started: "开始时间",
  Completed: "完成时间",
  "Raw Filters": "原始筛选条件",
  "Pydantic Schema JSON": "Pydantic 架构 JSON",
  "No memory exports": "暂无记忆导出",
  "Create an export to generate a downloadable JSON result.":
    "创建导出任务以生成可下载的 JSON 结果。",
  "Memory export created": "记忆导出已创建",
  "Failed to create export": "创建导出失败",
  "Check that filters and schema are valid JSON.":
    "请检查筛选条件和架构是否为有效 JSON。",
  "Export memories with raw filters and a Pydantic-style schema.":
    "使用原始筛选条件和 Pydantic 风格架构导出记忆。",
  "Download JSON": "下载 JSON",

  "New API Key": "新建 API 密钥",
  "Manage API Keys": "管理 API 密钥",
  "Easily create, view, and manage your API keys for seamless integration.":
    "轻松创建、查看和管理 API 密钥，实现无缝集成。",
  "Create API Key": "创建 API 密钥",
  "Create Key": "创建密钥",
  Create: "创建",
  Label: "标签",
  Key: "密钥",
  "Key Name": "密钥名称",
  "YiQiao Default Key": "YiQiao 默认密钥",
  Prefix: "前缀",
  "Last used": "最后使用",
  "Last Used": "最后使用",
  "Created at": "创建时间",
  "Created At": "创建时间",
  Permissions: "权限",
  "Expires At": "到期时间",
  None: "无",
  Legacy: "旧版兼容",
  Never: "从未使用",
  Revoke: "撤销",
  "Revoke API key": "撤销 API 密钥",
  "Failed to create key": "创建密钥失败",
  "Failed to revoke key": "撤销密钥失败",
  "Failed to load API keys": "加载 API 密钥失败",
  "API key revoked": "API 密钥已撤销",
  "API key created": "API 密钥已创建",
  "Copy API key": "复制 API 密钥",
  "Copy reset command": "复制重置密码命令",
  "Your API Key": "你的 API 密钥",
  "Project:": "项目：",
  "e.g. Production": "例如：生产环境",
  "Save this key -- you won't see it again.":
    "请保存这个密钥，之后不会再次显示。",
  "API keys are securely hashed and cannot be viewed again. If you did not save a key, create a new one and update your integration.":
    "API 密钥会经过安全哈希处理，无法再次查看。如果未保存密钥，请新建一个并更新你的集成。",
  Done: "完成",
  "No API keys yet": "暂无 API 密钥",
  "No API keys found.": "未找到 API 密钥。",
  "Create your first API key to start using the YiQiao API.":
    "创建第一个 API 密钥以开始使用 YiQiao API。",
  "Applications using this key will immediately stop working. This cannot be undone.":
    "使用此密钥的应用会立即停止工作。此操作无法撤销。",

  "Save Configuration": "保存配置",
  "Configuration saved": "配置已保存",
  "Failed to save configuration": "保存配置失败",
  "Failed to load server configuration": "加载服务器配置失败",
  "Failed to load bundled providers": "加载内置提供商失败",
  "Loading effective server configuration...": "正在加载有效的服务器配置...",
  "Need another provider? Install its Python package, rebuild the image, and extend the bundled list. See the":
    "需要其他提供商？安装对应 Python 包、重建镜像，并扩展内置列表。查看",
  "setup guide": "设置指南",
  "Embedding Model": "嵌入模型",
  LLM: "大语言模型",
  Embedding: "嵌入模型",
  Rerank: "重排模型",
  Provider: "提供商",
  "Base URL": "基础 URL",
  "Model ID": "模型 ID",
  Dimensions: "向量维度",
  "API key": "API 密钥",
  "Configured - leave blank to keep": "已配置，留空则保持不变",
  "Model ID is required": "请填写模型 ID",
  "Dimensions must be positive": "向量维度必须为正整数",
  "Top K must be positive": "Top K 必须为正整数",
  "LLM and embedding model IDs are required": "请填写 LLM 和嵌入模型 ID",
  "Model test passed": "模型测试通过",
  "Model test failed": "模型测试失败",
  Connected: "连接成功",
  "Model Name": "模型名称",
  "Save settings": "保存设置",
  "Save workspace settings": "保存工作区设置",
  "Workspace settings saved": "工作区设置已保存",
  Projects: "项目",
  "Project settings": "项目设置",
  "Account settings": "账号设置",
  New: "新建",
  "Project Name": "项目名称",
  "Project Description": "项目描述",
  "Enter project description...": "输入项目描述...",
  "Organization Name": "组织名称",
  General: "常规",
  Retention: "保留策略",
  "Create New Project": "新建项目",
  "Create New Organization": "新建组织",
  "Invite Member": "邀请成员",
  "Project ID": "项目 ID",
  "Organization ID": "组织 ID",
  "Copy project ID": "复制项目 ID",
  "Copy organization ID": "复制组织 ID",
  Default: "默认",
  "Member Name": "成员名称",
  "Seat Type": "席位类型",
  "Can Read": "可读取",
  "Can Edit": "可编辑",
  "Danger Zone": "危险区域",
  "Delete all memories": "删除所有记忆",
  "Delete All Memories": "删除所有记忆",
  "Permanently remove every memory in this project. This cannot be undone.":
    "永久删除此项目中的所有记忆。此操作无法撤销。",
  "Permanently delete this project and all data associated with it.":
    "永久删除此项目及其全部关联数据。",
  "Permanently delete this organization and all projects, members, and data within it.":
    "永久删除此组织及其中的所有项目、成员和数据。",
  "Create New": "新建",
  "Search for organization": "搜索组织",
  "Search for project": "搜索项目",
  "Active Organization": "当前组织",
  "Active Project": "当前项目",
  "All projects": "全部项目",
  Members: "成员",
  Member: "成员",
  Admin: "管理员",
  Owner: "所有者",
  invited: "已邀请",
  active: "已激活",
  Add: "添加",
  Adding: "添加中",
  "Adding...": "添加中...",
  "Member invited": "成员已邀请",
  "Member removed": "成员已移除",
  "Member role updated": "成员角色已更新",
  "Organization created": "组织已创建",
  "Organization deleted": "组织已删除",
  "Project created": "项目已创建",
  "Project deleted": "项目已删除",
  "Failed to create organization": "创建组织失败",
  "Failed to delete organization": "删除组织失败",
  "Failed to create project": "创建项目失败",
  "Failed to delete project": "删除项目失败",
  "Failed to update member role": "更新成员角色失败",
  Organization: "组织",
  Org: "组织",
  "Delete organization": "删除组织",
  "Delete Organization": "删除组织",
  "Delete project": "删除项目",
  "Delete Project": "删除项目",
  "No projects": "暂无项目",
  "Create a project first": "请先创建项目",
  "teammate@example.com": "teammate@example.com",
  Extraction: "提取",
  "Multilingual Memory Extraction": "多语言记忆提取",
  "Memories stored in your input language.": "记忆将使用输入语言存储。",
  "Select Usecase": "选择使用场景",
  "Choose Memory Depth": "选择记忆深度",
  "Specify any additional elements you want to include in your instructions":
    "指定希望指令中包含的其他内容",
  "Specify any elements you want to exclude from your instructions":
    "指定希望从指令中排除的内容",
  "Enter any specific data points, formats, or information you want to include...":
    "输入希望包含的数据点、格式或信息...",
  "Enter any data points, formats, or information you want to exclude...":
    "输入希望排除的数据点、格式或信息...",
  "Enter custom instructions...": "输入自定义指令...",
  "Hide Details": "隐藏详情",
  "Show Details": "显示详情",
  Inclusions: "包含项",
  Exclusions: "排除项",
  "Save Changes": "保存更改",
  Usecase: "使用场景",
  "Select a usecase": "选择使用场景",
  "Memory Depth": "记忆深度",
  "Essential Insights": "核心洞察",
  "Balanced Context": "均衡上下文",
  "Comprehensive Knowledge": "完整知识",
  Include: "包含",
  Exclude: "排除",
  "Data points, formats, or information to include":
    "需要包含的数据点、格式或信息",
  "Data points, formats, or information to exclude":
    "需要排除的数据点、格式或信息",
  "Skip to Manual Customization": "跳到手动自定义",
  "Generate Instructions": "生成指令",
  "Instructions generated": "指令已生成",
  "Custom Instructions": "自定义指令",
  "Manual custom instructions": "手动自定义指令",
  "Categories and Retention": "分类和保留策略",
  "Categories & Retention": "分类和保留策略",
  "Categories / Retention": "分类和保留策略",
  "Sports: Anything related to sports": "体育：任何与体育相关的内容",
  "Memory Decay": "记忆衰减",
  "Memory Expiration Date": "记忆到期日期",
  "Pick a date": "选择日期",
  "Clear date": "清除日期",
  "No custom categories present. Add a category to get started.":
    "暂无自定义分类。添加一个分类即可开始。",
  "Memory decay": "记忆衰减",
  Expiration: "过期时间",
  "Last saved": "上次保存",
  "No expiration date": "永不过期",
  "Not saved in this session": "本次会话尚未保存",
  "Generate categories": "生成分类",
  "Categories generated": "分类已生成",
  "Failed to generate categories": "生成分类失败",
  "No categories were generated.": "未生成任何分类。",
  "Custom instructions": "自定义指令",
  "Force add only": "仅强制新增",
  Reranking: "重排",
  Temperature: "温度",
  Threshold: "阈值",
  "Top P": "Top P",
  "Top K": "Top K",
  "Max tokens": "最大令牌数",
  "Invalid playground settings": "调试台设置无效",
  "Temperature must be between 0 and 2.": "温度必须在 0 到 2 之间。",
  "Threshold must be between 0 and 1.": "阈值必须在 0 到 1 之间。",
  "Top P must be between 0 and 1.": "Top P 必须在 0 到 1 之间。",
  "Top K must be a positive integer.": "Top K 必须是正整数。",
  "Max tokens must be a positive integer.": "最大令牌数必须是正整数。",
  "Create or select a project before saving": "请先创建或选择项目再保存",
  "Workspace saved, category generation failed": "工作区已保存，但生成分类失败",
  "Top K must not exceed 100.": "Top K 不能超过 100。",
  "Max tokens must not exceed 131072.": "最大令牌数不能超过 131072。",
  "Project name is required": "项目名称为必填项",
  "Settings saved": "设置已保存",
  "Failed to save settings": "保存设置失败",
  Profile: "个人资料",
  "First Name": "名字",
  "Last Name": "姓氏",
  Authorization: "账户操作",
  Logout: "退出登录",
  "Delete Account": "删除账户",
  "Save profile": "保存个人资料",
  "Profile updated": "个人资料已更新",
  "Current password": "当前密码",
  "New password": "新密码",
  "Confirm new password": "确认新密码",
  "Passwords don't match": "两次输入的密码不一致",
  "Update password": "更新密码",
  "Password updated": "密码已更新",
  Appearance: "外观",
  Theme: "主题",
  "Light theme": "浅色主题",
  "Dark theme": "深色主题",
  "System theme": "跟随系统主题",
  "Failed to load workspace settings": "加载工作区设置失败",
  "Failed to update profile": "更新个人资料失败",
  "Failed to update password": "更新密码失败",
  "Failed to invite member": "邀请成员失败",
  "Failed to remove member": "移除成员失败",
  "Failed to save workspace settings": "保存工作区设置失败",
  "Failed to generate instructions": "生成指令失败",

  Healthcare: "医疗健康",
  "AI Companion": "AI 陪伴",
  "Customer Support": "客户支持",
  "E-commerce": "电子商务",
  Education: "教育",
  Personal: "个人",
  CODING_AGENT: "编程智能体",
  VOICE_AGENT: "语音智能体",
  OPENCLAW: "OpenClaw",
  ENTERPRISE_SAAS: "企业 SaaS",
  Assistant: "助手",

  "Usage & limits": "用量与限制",
  "Usage & Limits": "用量与限制",
  "Failed to load usage scopes": "加载用量作用域失败",
  "Failed to load usage": "加载用量失败",
  "Failed to load limits": "加载限制失败",
  "Usage limits updated": "用量限制已更新",
  "Failed to update limits": "更新用量限制失败",
  "Owner access is required to manage usage limits.":
    "只有所有者可以管理用量限制。",
  "Organization and project scope": "组织和项目作用域",
  "Organization membership": "组织成员",
  "Project membership": "项目成员",
  "Production memory extraction": "生产环境记忆提取",
  "Production categories and new-memory expiration":
    "生产环境分类和新记忆过期策略",
  "Playground requests only": "仅作用于调试台请求",
  "Personal account": "个人账号",
  "Settings sections": "设置分区",
  Scope: "作用域",
  Subject: "对象",
  "No subjects available": "暂无可用对象",
  Policies: "策略",
  "Default: Unlimited": "默认：无限制",
  Limited: "有限制",
  Unlimited: "无限制",
  Limit: "上限",
  Period: "周期",
  Total: "总量",
  "Per minute": "每分钟",
  "Per day": "每天",
  "Per month": "每月",
  Mode: "模式",
  Monitor: "仅监控",
  Soft: "软限制",
  Hard: "硬限制",
  "Warn at %": "预警阈值 %",
  Saving: "保存中",
  "Save limits": "保存限制",
  "Manage limits": "管理限制",
  "Manage usage limits": "管理用量限制",
  "Current project": "当前项目",
  "Stored memories": "已存储记忆",
  "Memory writes": "记忆写入",
  "Memory searches": "记忆搜索",
  "API requests": "API 请求",
  "Request activity": "请求活动",
  "Effective limits": "生效的限制",
  Attribution: "归因统计",
  "API keys": "API 密钥",
  "No attributed API key requests in this period.":
    "此周期内没有可归因到 API 密钥的请求。",
  "No attributed member requests in this period.":
    "此周期内没有可归因到成员的请求。",
  "Model tokens: unavailable from the configured provider.":
    "当前提供商无法提供模型令牌用量。",
  "7 days": "7 天",
  "30 days": "30 天",
  "90 days": "90 天",
  Writes: "写入",
  Searches: "搜索",
  requests: "次请求",
  to: "至",
  per: "每",
  minute: "分钟",
  day: "天",
  month: "月",
  total: "总量",
  monitor: "仅监控",
  soft: "软限制",
  hard: "硬限制",

  "User ID": "用户 ID",
  "User ID is required": "请输入用户 ID",
  "Agent ID": "智能体 ID",
  "App ID": "应用 ID",
  "Run ID": "运行 ID",
  user: "用户",
  agent: "智能体",
  app: "应用",
  run: "运行",
  completed: "已完成",
  pending: "等待中",
  failed: "失败",
  success: "成功",
  "memory.added": "记忆已添加",
  "memory.updated": "记忆已更新",
  "memory.deleted": "记忆已删除",
  "memory.categorized": "记忆已分类",
  "Create webhook": "创建 Webhook",
  ms: "毫秒",

  "Default expiration": "默认过期策略",
  "Apply default expiration": "应用默认过期策略",
  "Default expiration date": "默认过期日期",
  "Reply instructions": "回复指令",
  "Instructions used only for Playground replies": "仅用于调试台回复的指令",
  "Reply temperature": "回复温度",
  "Similarity threshold": "相似度阈值",
  "Reply Top P": "回复 Top P",
  "Retrieved memories": "检索记忆数量",
  "Reply max tokens": "回复最大令牌数",
  "Store raw messages without extraction": "不经提取直接存储原始消息",
  "Rerank retrieved memories": "重排检索到的记忆",

  "Could not read server configuration": "无法读取服务器配置",
  "Password must be at least 8 characters": "密码至少需要 8 个字符",
  "Registration failed": "注册失败",
  "Failed to create API key": "创建 API 密钥失败",
  "Test failed": "测试失败",

  "Failed to load memories": "加载记忆失败",
  "Failed to load request logs": "加载请求日志失败",
  "Failed to load entities": "加载实体失败",
  "Failed to load graph": "加载图谱失败",
  "Failed to sync graph": "同步图谱失败",
  "Syncing...": "同步中...",
  "Graph entities could not be loaded:": "无法加载图谱实体：",
  "Graph is unavailable": "图谱不可用",
  "Neo4j is unavailable": "Neo4j 不可用",
  "No graph data": "暂无图谱数据",
  "Add memories with user_id, agent_id, app_id, or run_id, then sync Neo4j.":
    "添加带有 user_id、agent_id、app_id 或 run_id 的记忆后同步 Neo4j。",
  "Set NEO4J_ENABLED=true and fill Neo4j credentials in server/.env, then restart the API.":
    "设置 NEO4J_ENABLED=true 并在 server/.env 中填写 Neo4j 凭据，然后重启 API。",
  "Neo4j password is not configured": "未配置 Neo4j 密码",
  "Neo4j driver initialization failed": "Neo4j 驱动初始化失败",
  "Neo4j graph upsert failed": "Neo4j 图谱写入失败",
  "Neo4j graph delete failed": "Neo4j 图谱删除失败",
  "Neo4j graph bulk delete failed": "Neo4j 图谱批量删除失败",
  "Neo4j related memory query failed": "Neo4j 相关记忆查询失败",
  "Neo4j status query failed": "Neo4j 状态查询失败",
  "Neo4j entity list failed": "Neo4j 实体列表加载失败",
  "Neo4j neighbor query failed": "Neo4j 相邻记忆查询失败",
  "Neo4j graph fetch failed": "Neo4j 图谱加载失败",
  "Category saved": "分类已保存",
  "Entity deleted": "实体已删除",
  "Memory deleted": "记忆已删除",
  "Memory updated": "记忆已更新",
  "Test webhook sent": "测试 Webhook 已发送",
  "Failed to create webhook": "创建 Webhook 失败",
  "Failed to update webhook": "更新 Webhook 失败",
  "Failed to delete webhook": "删除 Webhook 失败",
  "Failed to test webhook": "测试 Webhook 失败",
  "Failed to load webhooks": "加载 Webhook 失败",
  "Manage your project's webhooks to receive notifications for various events.":
    "管理项目的 Webhook，以接收各种事件的通知。",
  "Add New Webhook": "添加新 Webhook",
  "Enter webhook name": "输入 Webhook 名称",
  "Events (at least one required)": "事件（至少选择一项）",
  "Add Webhook": "添加 Webhook",
  "No webhooks yet": "暂无 Webhook",
  "No webhooks found.": "未找到 Webhook。",
  "Create a webhook endpoint to receive signed YiQiao events.":
    "创建 Webhook 端点以接收已签名的 YiQiao 事件。",
  "Deliver signed events when memories or searches change.":
    "当记忆或搜索发生变化时发送已签名事件。",
  Endpoint: "端点",
  "Endpoint URL": "端点 URL",
  "Last Delivery": "最后投递",
  On: "开",
  Off: "关",
  Test: "测试",
  "Memory sync": "记忆同步",
  "Add Memory": "添加记忆",
  "Update Memory": "更新记忆",
  "Categorize Memory": "分类记忆",
  "New hooks are signed with HMAC SHA-256 in X-YiQiao-Signature. Created":
    "新 Webhook 会在 X-YiQiao-Signature 中使用 HMAC SHA-256 签名。创建于",

  "Copy command": "复制命令",
  "Install YiQiao": "安装 YiQiao",
  "YiQiao SDK": "YiQiao SDK",
  "Use the Python SDK in your application.": "在你的应用中使用 Python SDK。",
  "API Reference": "接口文档",
  Synchronous: "同步",
  Asynchronous: "异步",
  "Step 1: Install the SDK": "第 1 步：安装 SDK",
  "Install the published YiQiao package from PyPI.":
    "从 PyPI 安装已发布的 YiQiao 软件包。",
  "Step 2: Add and search memories": "第 2 步：添加并检索记忆",
  "The SDK reads provider credentials such as OPENAI_API_KEY from your environment.":
    "SDK 会从环境变量中读取 OPENAI_API_KEY 等模型服务商凭据。",
  "Agent Harness": "智能体框架",
  "Memory across every session": "跨会话保留记忆",
  Integrations: "集成",
  "Drop into your existing SDK": "直接接入现有 SDK",
  "Memory for your workflow": "为工作流提供记忆能力",
  Prompt: "提示词",
  "View Docs": "查看文档",
  "API Docs": "API 文档",
  "Step 1: Install": "第 1 步：安装",
  "Step 1: Verify Node.js": "第 1 步：验证 Node.js",
  "Step 2: Initialize": "第 2 步：初始化",
  "Step 3: Add a memory": "第 3 步：添加记忆",
  "Step 4: Retrieve memories": "第 4 步：检索记忆",
  "Step 1: Set credentials": "第 1 步：设置凭据",
  "Step 2: Verify the API": "第 2 步：验证 API",
  "Step 2: Check YiQiao health": "第 2 步：检查 YiQiao 运行状态",
  "Install and activate": "安装并启用",
  "Connect YiQiao": "连接 YiQiao",
  "Install Hermes Agent": "安装 Hermes Agent",
  "Configure self-hosted memory": "配置自托管记忆",
  "Step 1: Add the plugin marketplace": "第 1 步：添加插件市场",
  "Step 2: Install the plugin": "第 2 步：安装插件",
  "Failed to load memory exports": "加载记忆导出失败",
  Analytics: "分析",
  "Usage, latency, and memory growth from this YiQiao instance.":
    "此 YiQiao 实例的用量、延迟和记忆增长。",
  "Avg Latency": "平均延迟",
  Success: "成功",
  "Requests over 7 days": "近 7 天请求数",
  Users: "用户",
  Agents: "智能体",
  Runs: "运行",
  "Stored Memories": "已存储记忆",
  "Add Requests": "添加请求",
  "Retrieval Requests": "检索请求",
  "Self-hosted Plan": "自托管方案",
  "Included locally": "本地已包含",
  "External costs": "外部成本",
  "Local usage counters for this self-hosted YiQiao instance.":
    "此自托管 YiQiao 实例的本地用量计数。",
  "API keys, webhooks, memory exports, Neo4j graph, playground, and project settings run inside your deployment.":
    "API 密钥、Webhook、记忆导出、Neo4j 图谱、调试台和项目设置都在你的部署中运行。",
  "Model, embedding, Postgres, and Neo4j hosting costs depend on the providers you configure in .env.":
    "模型、嵌入、Postgres 和 Neo4j 托管成本取决于你在 .env 中配置的提供商。",
  "Add memory with curl": "使用 curl 添加记忆",
  "Add memory with Python": "使用 Python 添加记忆",
  "Search with JavaScript": "使用 JavaScript 搜索",
  "API URL:": "API URL：",
  "Required headers": "必需请求头",
  "from API Keys": "来自 API 密钥",
  "Back to Dashboard": "返回仪表盘",
  "Playground request failed": "调试台请求失败",
  "Test memory addition and retrieval with your local model configuration.":
    "使用本地模型配置测试记忆添加和检索。",
  "Type a message": "输入消息",
  "Send message": "发送消息",
  "Relevant memories appear after a search.": "搜索后会显示相关记忆。",
  "Go to previous page": "上一页",
  "Go to next page": "下一页",
  "More pages": "更多页面",
  pagination: "分页",
  Notification: "通知",
  Notifications: "通知",
  "Notifications alt+T": "通知 alt+T",
  Unauthorized: "未授权",
  "Request failed": "请求失败",

  LEARN: "学习",
  Docs: "文档",
  "Memory API": "记忆 API",
  Cookbooks: "实战指南",
  "Select organization": "选择组织",
  "Select project": "选择项目",
  Account: "账号",
  Plugin: "插件",

  "All time": "全部时间",
  "All Time": "全部时间",
  "all time": "全部时间",
  "Last hour": "最近 1 小时",
  "Last 6 hours": "最近 6 小时",
  "Last 12 hours": "最近 12 小时",
  "Last day": "最近 1 天",
  "Last 7 days": "最近 7 天",
  "Last 14 days": "最近 14 天",
  "Last 30 days": "最近 30 天",
  "Last 90 days": "最近 90 天",
  Custom: "自定义",
  Overview: "概览",
  Filters: "筛选",
  Action: "操作",
  Actions: "操作",
  Entity: "实体",
  Event: "事件",
  Succeeded: "成功",
  Failed: "失败",
  succeeded: "成功",
  Apps: "应用",
  "1 day": "1 天",
  "selected range": "所选范围",

  "Search entity ID": "搜索实体 ID",
  "Refresh entities": "刷新实体",
  "Entities appear when memories are stored with matching identifiers.":
    "使用匹配标识符存储记忆后，实体会显示在这里。",
  memories: "条记忆",
  "No activity": "暂无活动",
  "Failed to load entity": "加载实体失败",
  "Failed to load entity requests": "加载实体请求失败",
  "Memory Content": "记忆内容",
  "Memory ID": "记忆 ID",
  "Invalid entity": "无效实体",
  "The entity type or identifier in this URL is invalid.":
    "URL 中的实体类型或标识符无效。",
  "Entity not found": "未找到实体",
  "This entity does not exist in the active project.":
    "当前项目中不存在此实体。",
  "Total Memories": "记忆总数",
  "Search memories": "搜索记忆",
  "Refresh memories": "刷新记忆",
  "Clean duplicate memories": "清理重复记忆",
  "Clean duplicates": "清理重复项",
  "Cleaning...": "正在清理...",
  "Failed to clean duplicate memories": "清理重复记忆失败",
  "YiQiao will keep the oldest memory in each exact duplicate group and permanently remove the rest. Different entity scopes are never merged.":
    "YiQiao 会保留每组完全重复记忆中最早的一条，并永久删除其余记忆。不同实体作用域的记忆不会被合并。",
  "No memories found": "未找到记忆",
  "Memories stored for this entity will appear here.":
    "为此实体存储的记忆将显示在这里。",
  "No requests found": "未找到请求",
  "Requests associated with this entity will appear here.":
    "与此实体关联的请求将显示在这里。",

  "All memories": "所有记忆",
  "Failed to download export": "下载导出失败",
  "Download export": "下载导出",
  "Export memories in a structured format using customizable Pydantic schemas.":
    "使用可自定义的 Pydantic 架构，以结构化格式导出记忆。",
  "Learn more": "了解更多",
  "Search by ID or entity...": "按 ID 或实体搜索...",
  "Search memory exports": "搜索记忆导出",
  "No matching memory exports found": "未找到匹配的记忆导出",
  "No memory exports found": "未找到记忆导出",
  "Previous page": "上一页",
  "Next page": "下一页",
  Page: "页码",
  "Create Memory Export": "创建记忆导出",
  "Configure memory export filters, dates, and output schema.":
    "配置记忆导出的筛选条件、日期和输出架构。",
  "Close create export": "关闭创建导出面板",
  Visual: "可视化",
  Raw: "原始",
  "Entity Filters": "实体筛选",
  "Entity type": "实体类型",
  "Enter ID...": "输入 ID...",
  "Remove entity filter": "移除实体筛选",
  "Remove filter": "移除筛选条件",
  "Add Filter": "添加筛选条件",
  "Invalid JSON object": "无效的 JSON 对象",
  "Date Range (Optional)": "日期范围（可选）",
  Clear: "清除",
  "Pick a date range": "选择日期范围",
  "Pydantic Schema": "Pydantic 架构",
  "Drag the bottom edge to resize": "拖动底边调整大小",

  ADD: "新增",
  SEARCH: "搜索",
  "GET ALL": "获取全部",
  "Has Results": "有结果",
  "Failed to load request activity": "加载请求活动失败",
  "Custom range start": "自定义范围开始日期",
  "Custom range end": "自定义范围结束日期",
  "Entity ID": "实体 ID",
  "Any status": "任意状态",
  "Request activity will appear here.": "请求活动将显示在这里。",
  "Request activity matching this view will appear here.":
    "符合当前视图的请求活动将显示在这里。",
  "Request payload and retrieved memories": "请求载荷和检索到的记忆",
  "Request Payload": "请求载荷",
  "Retrieved Memories": "检索到的记忆",
  "Search Query": "搜索查询",
  "Requested At": "请求时间",
  Payload: "载荷",
  "No response payload was recorded for this request.":
    "未记录此请求的响应载荷。",
  "request ID": "请求 ID",
  "request payload": "请求载荷",
  "retrieved memories": "检索到的记忆",

  "No strong memory match": "没有高度匹配的记忆",
  "Couldn't save this memory": "无法保存这条记忆",
  "Conflicting memories detected": "检测到冲突记忆",
  "Failed to load memory details": "加载记忆详情失败",
  "Complete each filter": "请完整填写每个筛选条件",
  "Filter values and metadata keys cannot be empty.":
    "筛选值和元数据键不能为空。",
  "Feedback saved": "反馈已保存",
  "Failed to save feedback": "保存反馈失败",
  "Filter memories": "筛选记忆",
  Match: "匹配",
  all: "全部",
  any: "任一",
  conditions: "条件",
  "No filter conditions": "暂无筛选条件",
  "key or nested.key": "键或 nested.key",
  value: "值",
  "user ID": "用户 ID",
  "agent ID": "智能体 ID",
  "app ID": "应用 ID",
  "run ID": "运行 ID",
  "Category name": "分类名称",
  "Add filter": "添加筛选条件",
  "Clear all": "全部清除",
  "Apply filters": "应用筛选条件",
  "No matching memories": "没有匹配的记忆",
  "Adjust the active date range or filters.": "请调整当前日期范围或筛选条件。",
  "/ page": "条/页",
  "Memory Details": "记忆详情",
  "Inspect memory details, source messages, updates, and feedback.":
    "查看记忆详情、来源消息、更新和反馈。",
  "Previous memory": "上一条记忆",
  "Next memory": "下一条记忆",
  "Close details": "关闭详情",
  Details: "详情",
  "Source & Updates": "来源与更新",
  "Copy memory ID": "复制记忆 ID",
  "Created On": "创建时间",
  "Updated On": "更新时间",
  "No metadata available": "暂无元数据",
  Feedback: "反馈",
  "Positive feedback": "正向反馈",
  "Negative feedback": "负向反馈",
  "Write your feedback": "输入反馈",
  Source: "来源",
  "Scroll to see more": "滚动查看更多",
  Message: "消息",
  assistant: "助手",
  "No messages to show": "暂无消息",
  "Resize source and changelog panes": "调整来源与变更日志面板大小",
  "Drag to resize": "拖动调整大小",
  Changelog: "变更日志",
  "Copy changelog entry": "复制变更日志条目",
  "Changelog entry": "变更日志条目",
  at: "于",
  "No updates to show": "暂无更新",
  Enter: "回车",

  "Message is too long": "消息过长",
  "Maximum length": "最大长度",
  "User ID is too long": "用户 ID 过长",
  "New conversation": "新对话",

  "Failed to load dashboard usage": "加载仪表盘用量失败",
  "Failed to load dashboard entities": "加载仪表盘实体失败",
  Workspace: "工作台",
  "Monitor memory activity and project health at a glance.":
    "快速查看记忆活动与项目运行状态。",
  "No activity in this range": "此时间范围内暂无活动",
  "Chart legend": "图表图例",
  "Metric information": "指标说明",
  "Total Entities": "实体总数",
  "Memories currently stored in this project.": "当前项目中存储的记忆。",
  "Retrieval Events": "检索事件",
  "Add Events": "添加事件",
  "Retrieval events as a percentage of the active project limit.":
    "检索事件占当前项目上限的百分比。",
  "Dashboard date range": "仪表盘日期范围",
  "View Requests": "查看请求",
  "View Entities": "查看实体",
  "Explore the Platform": "探索平台",
  "Get the most out of YiQiao: tune it, see it in action, and ship faster.":
    "充分利用 YiQiao：完成调优、实际试用并更快交付。",
  "Customize YiQiao": "自定义 YiQiao",
  "Set what YiQiao remembers, how it is organized, and when it is used.":
    "设置 YiQiao 记住什么、如何组织记忆以及何时使用记忆。",
  "Integration Examples": "集成示例",
  "See real integration examples and patterns to add memory to your product.":
    "查看真实的集成示例和模式，为你的产品添加记忆能力。",
  "Try the Playground": "试用调试台",
  "Test memory addition and retrieval live before wiring it into your app.":
    "在接入应用前，实时测试记忆添加和检索。",
  "Use tested examples to add and search memories from your application.":
    "使用经过验证的示例从应用中添加和搜索记忆。",
  "Quick Start": "快速开始",
  View: "查看",
  "Try it": "试用",
  Open: "打开",
  "View Breakdown": "查看细分",
  Suggested: "推荐",

  Others: "其他",
  "Personalized Learning": "个性化学习",
  "Can make standard API requests and read basic data.":
    "可发起标准 API 请求并读取基础数据。",
  "Can add, update, delete memories and manage entities.":
    "可添加、更新、删除记忆并管理实体。",
  "Full access including members and project settings.":
    "拥有完整权限，包括成员和项目设置。",
  "Project updated": "项目已更新",
  "Failed to update project": "更新项目失败",
  "Organization updated": "组织已更新",
  "Failed to update organization": "更新组织失败",
  "Failed to create workspace": "创建工作区失败",
  "All project memories deleted": "已删除项目中的所有记忆",
  "Failed to delete memories": "删除记忆失败",
  "Extraction settings saved": "提取设置已保存",
  "Failed to save extraction settings": "保存提取设置失败",
  "Categories saved": "分类已保存",
  "Failed to save categories": "保存分类失败",
  "Retention settings saved": "保留策略已保存",
  "Failed to save retention settings": "保存保留策略失败",
  "Delete your YiQiao account and revoke all of its credentials?":
    "确定删除你的 YiQiao 账户并撤销其所有凭据吗？",
  "Failed to delete account": "删除账户失败",
  "Invitation pending": "邀请待接受",
  "Instructions copied": "指令已复制",
  Reconfigure: "重新配置",
  "Saving project...": "正在保存项目...",
  "No custom extraction instructions.": "暂无自定义提取指令。",
  "Not selected": "未选择",
  "e.g. Sports": "例如：体育",
  "e.g. Anything related to sports, teams, or athletes":
    "例如：与体育、球队或运动员有关的内容",
  "No description": "暂无描述",
  "Rank recently-accessed memories higher in search and gently down-rank idle ones. Nothing is deleted; older memories still surface when relevant.":
    "在搜索中提高近期访问记忆的排名，并适度降低长期未访问记忆的排名。不会删除任何内容，旧记忆在相关时仍会出现。",
  "Saving organization...": "正在保存组织...",
  "Invite a new member to your": "邀请新成员加入你的",
  organization: "组织",
  project: "项目",
  "Enter member email ID": "输入成员邮箱",
  "Enter the member's email address": "输入成员的邮箱地址",
  Role: "角色",
  "Inviting...": "邀请中...",
  "Invite to Organization": "邀请加入组织",
  "Invite to Project": "邀请加入项目",
  "Choose a name. It can be changed later in General settings.":
    "选择名称，稍后可在“常规”设置中修改。",

  "No data": "暂无数据",
  "No results": "暂无结果",
  Loading: "加载中",
  "Loading...": "加载中...",
  Copied: "已复制",
  Copy: "复制",
  "Copy code": "复制代码",
  Delete: "删除",
  "Enter name to confirm": "输入名称以确认",
  "Please type": "请输入",
  "to confirm.": "以确认。",
  Cancel: "取消",
  Confirm: "确认",
  Save: "保存",
  Edit: "编辑",
  Close: "关闭",
  "Try again": "重试",
  "Toggle theme": "切换主题",
  Light: "浅色",
  Dark: "深色",
  System: "跟随系统",
  "This YiQiao feature is available in this self-hosted dashboard.":
    "此 YiQiao 功能可在当前自托管仪表盘中使用。",
  "Open dashboard": "打开仪表盘",
};

const translatableAttributes = [
  "placeholder",
  "title",
  "aria-label",
  "content",
] as const;

type TranslationState = {
  source: string;
  translated?: string;
};

const textState = new WeakMap<Text, TranslationState>();
const attributeState = new WeakMap<
  Element,
  Partial<Record<(typeof translatableAttributes)[number], TranslationState>>
>();
let documentTitleState: TranslationState | null = null;

type GuardedNodePrototype = Node & {
  __yiqiaoDomMutationGuardsInstalled?: boolean;
};

function installDomMutationGuards() {
  if (typeof window === "undefined" || !window.Node) return;

  const nodePrototype = window.Node.prototype as GuardedNodePrototype;
  if (nodePrototype.__yiqiaoDomMutationGuardsInstalled) return;

  const originalRemoveChild = nodePrototype.removeChild;
  const originalInsertBefore = nodePrototype.insertBefore;

  nodePrototype.removeChild = function <T extends Node>(
    this: Node,
    child: T,
  ): T {
    if (child.parentNode !== this) {
      return child;
    }
    return originalRemoveChild.call(this, child) as T;
  };

  nodePrototype.insertBefore = function <T extends Node>(
    this: Node,
    newNode: T,
    referenceNode: Node | null,
  ): T {
    if (referenceNode && referenceNode.parentNode !== this) {
      return originalInsertBefore.call(this, newNode, null) as T;
    }
    return originalInsertBefore.call(this, newNode, referenceNode) as T;
  };

  nodePrototype.__yiqiaoDomMutationGuardsInstalled = true;
}

installDomMutationGuards();

type I18nContextValue = {
  language: Language;
  setLanguage: (language: Language) => void;
  toggleLanguage: () => void;
  t: (text: string) => string;
};

const I18nContext = createContext<I18nContextValue | null>(null);

const relativeTimeUnits: Record<string, string> = {
  second: "秒",
  minute: "分钟",
  hour: "小时",
  day: "天",
  week: "周",
  month: "个月",
  year: "年",
};

const relativeTimeQualifiers: Record<string, string> = {
  about: "大约",
  over: "超过",
  almost: "将近",
};

function normalizeText(text: string) {
  return text.trim().replace(/\s+/g, " ");
}

function translateCount(value: string) {
  if (value === "a" || value === "an") return "1";
  if (value === "half a") return "半";
  return value;
}

function translateRelativeTime(text: string) {
  const normalized = normalizeText(text).toLowerCase();

  if (normalized === "yesterday") return "昨天";
  if (normalized === "today") return "今天";
  if (normalized === "tomorrow") return "明天";

  const compactPast = normalized.match(/^(\d+)(s|m|h|d|mo|y) ago$/);
  if (compactPast) {
    const compactUnits: Record<string, string> = {
      s: "秒",
      m: "分钟",
      h: "小时",
      d: "天",
      mo: "个月",
      y: "年",
    };
    return `${compactPast[1]} ${compactUnits[compactPast[2]]}前`;
  }

  const past = normalized.match(
    /^(?:(about|over|almost) )?(less than a|half a|an?|[0-9]+) (second|minute|hour|day|week|month|year)s? ago$/,
  );
  if (past) {
    const [, qualifier, count, unit] = past;
    const translatedUnit = relativeTimeUnits[unit];
    if (!translatedUnit) return null;
    if (count === "less than a") return `不到 1 ${translatedUnit}前`;
    const prefix = qualifier ? `${relativeTimeQualifiers[qualifier]} ` : "";
    return `${prefix}${translateCount(count)} ${translatedUnit}前`;
  }

  const future = normalized.match(
    /^in (?:(about|over|almost) )?(less than a|half a|an?|[0-9]+) (second|minute|hour|day|week|month|year)s?$/,
  );
  if (future) {
    const [, qualifier, count, unit] = future;
    const translatedUnit = relativeTimeUnits[unit];
    if (!translatedUnit) return null;
    if (count === "less than a") return `不到 1 ${translatedUnit}后`;
    const prefix = qualifier ? `${relativeTimeQualifiers[qualifier]} ` : "";
    return `${prefix}${translateCount(count)} ${translatedUnit}后`;
  }

  return null;
}

const englishMonthNumbers: Record<string, number> = {
  jan: 1,
  feb: 2,
  mar: 3,
  apr: 4,
  may: 5,
  jun: 6,
  jul: 7,
  aug: 8,
  sep: 9,
  oct: 10,
  nov: 11,
  dec: 12,
};

function to24Hour(hourText: string, period: string) {
  const hour = Number(hourText) % 12;
  return String(period.toLowerCase() === "pm" ? hour + 12 : hour).padStart(
    2,
    "0",
  );
}

function translateEnglishDate(text: string) {
  const normalized = normalizeText(text);
  let match = normalized.match(
    /^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2})(?:, (\d{4}))?(?:(?:,| at) (\d{1,2}):(\d{2})(?::(\d{2}))? (AM|PM))?$/i,
  );
  if (match) {
    const [, monthName, day, year, hour, minute, second, period] = match;
    const date = `${year ? `${year}年` : ""}${englishMonthNumbers[monthName.toLowerCase()]}月${Number(day)}日`;
    if (!hour || !minute || !period) return date;
    const time = `${to24Hour(hour, period)}:${minute}${second ? `:${second}` : ""}`;
    return `${date} ${time}`;
  }

  match = normalized.match(
    /^(\d{1,2}):(\d{2})(?::(\d{2}))? (AM|PM), (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2}), (\d{4})$/i,
  );
  if (match) {
    const [, hour, minute, second, period, monthName, day, year] = match;
    const time = `${to24Hour(hour, period)}:${minute}${second ? `:${second}` : ""}`;
    return `${year}年${englishMonthNumbers[monthName.toLowerCase()]}月${Number(day)}日 ${time}`;
  }

  match = normalized.match(
    /^(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4}), (\d{1,2}):(\d{2})(?::(\d{2}))?$/i,
  );
  if (match) {
    const [, day, monthName, year, hour, minute, second] = match;
    return `${year}年${englishMonthNumbers[monthName.toLowerCase()]}月${Number(day)}日 ${hour.padStart(2, "0")}:${minute}${second ? `:${second}` : ""}`;
  }

  match = normalized.match(
    /^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2}) (\d{1,2}):(\d{2})(?::(\d{2}))?$/i,
  );
  if (match) {
    const [, monthName, day, hour, minute, second] = match;
    return `${englishMonthNumbers[monthName.toLowerCase()]}月${Number(day)}日 ${hour.padStart(2, "0")}:${minute}${second ? `:${second}` : ""}`;
  }

  match = normalized.match(
    /^(\d{1,2})\/(\d{1,2})\/(\d{4}), (\d{1,2}):(\d{2})(?::(\d{2}))? (AM|PM)$/i,
  );
  if (match) {
    const [, month, day, year, hour, minute, second, period] = match;
    const time = `${to24Hour(hour, period)}:${minute}${second ? `:${second}` : ""}`;
    return `${year}年${Number(month)}月${Number(day)}日 ${time}`;
  }

  match = normalized.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))? (AM|PM)$/i);
  if (match) {
    const [, hour, minute, second, period] = match;
    return `${to24Hour(hour, period)}:${minute}${second ? `:${second}` : ""}`;
  }

  return null;
}

function translateEnglishDateRange(text: string) {
  const match = normalizeText(text).match(/^(.+?)\s+-\s+(.+)$/);
  if (!match) return null;
  const start = translateEnglishDate(match[1]);
  const end = translateEnglishDate(match[2]);
  return start && end ? `${start} 至 ${end}` : null;
}

function translateRangeDescription(text: string) {
  const normalized = normalizeText(text);
  return (
    translations[normalized] ??
    translateEnglishDateRange(normalized) ??
    translateEnglishDate(normalized) ??
    normalized
  );
}

function translateDynamicText(text: string): string | null {
  let match = text.match(/^([\d,.]+) requests$/i);
  if (match) return `${match[1]} 次请求`;

  match = text.match(/^([\d,.]+) ms$/i);
  if (match) return `${match[1]} 毫秒`;

  match = text.match(/^([\d,.]+) s$/i);
  if (match) return `${match[1]} 秒`;

  match = text.match(/^([\d,.]+) dimensions$/i);
  if (match) return `${match[1]} 维`;

  match = text.match(/^([\d,.]+) results?$/i);
  if (match) return `${match[1]} 个结果`;

  match = text.match(/^(.+), ([\d,.]+) ms$/i);
  if (match) {
    return `${getTranslation(match[1]) ?? match[1]}，${match[2]} 毫秒`;
  }

  match = text.match(/^(.+) requires a positive whole-number limit$/i);
  if (match) {
    return `${getTranslation(match[1]) ?? match[1]}的上限必须是正整数`;
  }

  match = text.match(/^(.+) copied$/i);
  if (match) {
    const label = getTranslation(match[1]);
    return label ? `${label} 已复制` : null;
  }

  match = text.match(/^Copy (.+)$/i);
  if (match) {
    const label = getTranslation(match[1]);
    return label ? `复制${label}` : null;
  }

  match = text.match(/^No (user|agent|app|run) entities found$/i);
  if (match) {
    const entityType = translations[match[1].toLowerCase()] ?? match[1];
    return `未找到${entityType}实体`;
  }

  match = text.match(/^(User|Agent|App|Run): (.+)$/);
  if (match) return `${translations[match[1]]}：${match[2]}`;

  match = text.match(/^Retrieval API Usage \((.+)\)$/i);
  if (match) return `检索 API 用量（${translateRangeDescription(match[1])}）`;

  match = text.match(/^Successful memory (retrievals|writes) across (.+)\.$/i);
  if (match) {
    const action =
      match[1].toLowerCase() === "retrievals" ? "记忆检索" : "记忆写入";
    return `${translateRangeDescription(match[2])}内成功的${action}次数。`;
  }

  match = text.match(/^About (.+)$/i);
  if (match) {
    const label = getTranslation(match[1]);
    return label ? `关于${label}` : null;
  }

  match = text.match(/^View (requests|entities) breakdown$/i);
  if (match) {
    const label = match[1].toLowerCase() === "requests" ? "请求" : "实体";
    return `查看${label}细分`;
  }

  const translatedDateRange = translateEnglishDateRange(text);
  if (translatedDateRange) return translatedDateRange;

  match = text.match(/^Organization: (.+)$/i);
  if (match) return `组织：${match[1]}`;

  match = text.match(/^Open account menu for (.+)$/i);
  if (match) return `打开 ${match[1]} 的账户菜单`;

  match = text.match(
    /^(.+) (add|update|retrieved|delete|user) count(?: ([\d,.]+))?$/i,
  );
  if (match) {
    const labels: Record<string, string> = {
      add: "新增",
      update: "更新",
      retrieved: "检索",
      delete: "删除",
      user: "用户",
    };
    const count = match[3] ? `：${match[3]}` : "";
    return `${match[1]} ${labels[match[2].toLowerCase()]}数量${count}`;
  }

  match = text.match(/^Manage usage limits for (.+)$/i);
  if (match) return `管理 ${match[1]} 的用量限制`;

  match = text.match(/^Remove (.+) from this (organization|project)\?$/i);
  if (match) {
    const scope = match[2].toLowerCase() === "organization" ? "组织" : "项目";
    return `确定从此${scope}中移除 ${match[1]} 吗？`;
  }

  match = text.match(/^Remove (.+)$/i);
  if (match) return `移除 ${match[1]}`;

  match = text.match(/^Delete organization "(.+)" and all of its projects\?$/i);
  if (match) return `确定删除组织“${match[1]}”及其所有项目吗？`;

  match = text.match(/^Delete project "(.+)"\?$/i);
  if (match) return `确定删除项目“${match[1]}”吗？`;

  match = text.match(/^Delete project "(.+)" and all of its data\?$/i);
  if (match) return `确定删除项目“${match[1]}”及其全部数据吗？`;

  match = text.match(/^Delete organization "(.+)" and all projects in it\?$/i);
  if (match) return `确定删除组织“${match[1]}”及其中所有项目吗？`;

  match = text.match(
    /^Delete every memory in "(.+)"\? This cannot be undone\.$/i,
  );
  if (match) return `确定删除“${match[1]}”中的每条记忆吗？此操作无法撤销。`;

  match = text.match(/^Delete (.+)$/i);
  if (match) return `删除 ${match[1]}`;

  match = text.match(/^Test failed \((\d+)\):\s*(.+)$/i);
  if (match) {
    return `测试失败（${match[1]}）：${getTranslation(match[2]) ?? match[2]}`;
  }

  return null;
}

function getTranslation(text: string) {
  const key = normalizeText(text);
  return (
    translations[key] ??
    translateKnownErrorMessage(key) ??
    translateRelativeTime(key) ??
    translateDynamicText(key) ??
    translateEnglishDate(key)
  );
}

export function translateText(text: string, language: Language) {
  if (language === "en") return text;
  return getTranslation(text) ?? text;
}

function translateDocumentTitle(language: Language) {
  if (typeof document === "undefined" || !document.title) return;

  if (!documentTitleState) {
    documentTitleState = { source: document.title };
  } else if (
    document.title !== documentTitleState.source &&
    document.title !== documentTitleState.translated
  ) {
    documentTitleState = { source: document.title };
  }

  if (language === "en") {
    if (document.title !== documentTitleState.source) {
      document.title = documentTitleState.source;
    }
    return;
  }

  const translated = getTranslation(documentTitleState.source);
  if (translated) {
    documentTitleState.translated = translated;
    if (document.title !== translated) {
      document.title = translated;
    }
  }
}

function translateNodeText(node: Text, language: Language) {
  const current = node.textContent ?? "";
  if (!current.trim()) return;

  let state = textState.get(node);
  if (!state) {
    state = { source: current };
    textState.set(node, state);
  } else if (current !== state.source && current !== state.translated) {
    state.source = current;
    state.translated = undefined;
  }

  if (language === "en") {
    if (node.textContent !== state.source) node.textContent = state.source;
    return;
  }

  const leading = state.source.match(/^\s*/)?.[0] ?? "";
  const trailing = state.source.match(/\s*$/)?.[0] ?? "";
  const translated = getTranslation(state.source);
  if (translated) {
    state.translated = `${leading}${translated}${trailing}`;
    if (node.textContent !== state.translated) {
      node.textContent = state.translated;
    }
  }
}

function translateElementAttributes(element: Element, language: Language) {
  translatableAttributes.forEach((attribute) => {
    const value = element.getAttribute(attribute);
    if (!value) return;

    let states = attributeState.get(element);
    if (!states) {
      states = {};
      attributeState.set(element, states);
    }

    let state = states[attribute];
    if (!state) {
      state = { source: value };
      states[attribute] = state;
    } else if (value !== state.source && value !== state.translated) {
      state.source = value;
      state.translated = undefined;
    }

    if (language === "en") {
      if (element.getAttribute(attribute) !== state.source) {
        element.setAttribute(attribute, state.source);
      }
      return;
    }

    const translated = getTranslation(state.source);
    if (translated) {
      state.translated = translated;
      if (element.getAttribute(attribute) !== translated) {
        element.setAttribute(attribute, translated);
      }
    }
  });
}

function applyTranslations(root: ParentNode, language: Language) {
  if (typeof document === "undefined") return;

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      if (
        parent.closest(
          "script,style,code,pre,textarea,[data-i18n-ignore='true']",
        )
      ) {
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });

  const textNodes: Text[] = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode as Text);
  textNodes.forEach((node) => translateNodeText(node, language));

  if (root instanceof Element) translateElementAttributes(root, language);
  root
    .querySelectorAll?.("*")
    .forEach((element) => translateElementAttributes(element, language));
}

export function I18nProvider({
  children,
  initialLanguage,
}: {
  children: ReactNode;
  initialLanguage: Language;
}) {
  const [language, setLanguageState] = useState<Language>(initialLanguage);

  const setLanguage = useCallback((nextLanguage: Language) => {
    setLanguageState(nextLanguage);
    localStorage.setItem(LANGUAGE_PREFERENCE_KEY, nextLanguage);
    document.cookie = `${LANGUAGE_PREFERENCE_KEY}=${nextLanguage}; Path=/; Max-Age=31536000; SameSite=Lax`;
  }, []);

  useEffect(() => {
    installDomMutationGuards();
    localStorage.setItem(LANGUAGE_PREFERENCE_KEY, language);
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
    translateDocumentTitle(language);
    applyTranslations(document.head, language);
    applyTranslations(document.body, language);
    let routeAnnouncerRoot: ShadowRoot | null = null;
    let routeAnnouncerObserver: MutationObserver | null = null;
    const connectRouteAnnouncer = () => {
      const root = document.querySelector("next-route-announcer")?.shadowRoot;
      if (!root || root === routeAnnouncerRoot) return;
      routeAnnouncerObserver?.disconnect();
      routeAnnouncerRoot = root;
      applyTranslations(root, language);
      routeAnnouncerObserver = new MutationObserver(() => {
        applyTranslations(root, language);
      });
      routeAnnouncerObserver.observe(root, {
        characterData: true,
        childList: true,
        subtree: true,
      });
    };
    connectRouteAnnouncer();
    const observer = new MutationObserver((mutations) => {
      connectRouteAnnouncer();
      mutations.forEach((mutation) => {
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType === Node.TEXT_NODE) {
            translateNodeText(node as Text, language);
          } else if (node instanceof Element) {
            applyTranslations(node, language);
          }
        });
        if (
          mutation.type === "characterData" &&
          mutation.target.nodeType === Node.TEXT_NODE
        ) {
          translateNodeText(mutation.target as Text, language);
        }
        if (
          mutation.type === "attributes" &&
          mutation.target instanceof Element
        ) {
          translateElementAttributes(mutation.target, language);
        }
      });
    });
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: [...translatableAttributes],
      characterData: true,
      childList: true,
      subtree: true,
    });
    const titleObserver = new MutationObserver(() => {
      translateDocumentTitle(language);
      applyTranslations(document.head, language);
    });
    titleObserver.observe(document.head, {
      attributes: true,
      attributeFilter: ["content"],
      characterData: true,
      childList: true,
      subtree: true,
    });
    return () => {
      observer.disconnect();
      titleObserver.disconnect();
      routeAnnouncerObserver?.disconnect();
    };
  }, [language]);

  useEffect(() => {
    const originalConfirm = window.confirm;
    window.confirm = (message?: string) => {
      const source = String(message ?? "");
      const localized =
        language === "zh" ? (getTranslation(source) ?? source) : source;
      return originalConfirm.call(window, localized);
    };
    return () => {
      window.confirm = originalConfirm;
    };
  }, [language]);

  const value = useMemo<I18nContextValue>(
    () => ({
      language,
      setLanguage,
      toggleLanguage: () => setLanguage(language === "zh" ? "en" : "zh"),
      t: (text) => translateText(text, language),
    }),
    [language, setLanguage],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useI18n must be used within I18nProvider");
  }
  return context;
}
