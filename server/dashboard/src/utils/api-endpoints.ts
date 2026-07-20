// This file was modified in 2026 by YiQiao contributors. See NOTICE.

export const AUTH_ENDPOINTS = {
  SETUP_STATUS: "/auth/setup-status",
  REGISTER: "/auth/register",
  LOGIN: "/auth/login",
  REFRESH: "/auth/refresh",
  ME: "/auth/me",
  DELETE_ACCOUNT: "/auth/me",
  CHANGE_PASSWORD: "/auth/change-password",
  ONBOARDING_COMPLETE: "/auth/onboarding-complete",
} as const;

export const MEMORY_ENDPOINTS = {
  BASE: "/memories",
  QUERY: "/memories/query",
  BY_ID: (memoryId: string) => `/memories/${memoryId}`,
  DETAILS: (memoryId: string) => `/memories/${memoryId}/details`,
  FEEDBACK: (memoryId: string) => `/memories/${memoryId}/feedback`,
  HISTORY: (memoryId: string) => `/memories/${memoryId}/history`,
  CONFIGURE: "/configure",
  CONFIGURE_PROVIDERS: "/configure/providers",
  CONFIGURE_TEST: "/configure/test",
  RESET: "/reset",
  GENERATE_INSTRUCTIONS: "/generate-instructions",
  GENERATE_CATEGORIES: "/generate-categories",
} as const;

export const MEMORY_IMPORT_ENDPOINTS = {
  BASE: "/memory-imports",
  BY_ID: (jobId: string) => `/memory-imports/${jobId}`,
  CANCEL: (jobId: string) => `/memory-imports/${jobId}/cancel`,
  ERRORS: (jobId: string) => `/memory-imports/${jobId}/errors`,
  RETRY: (jobId: string) => `/memory-imports/${jobId}/retry`,
  GRAPH_RETRY: (jobId: string) => `/memory-imports/${jobId}/graph-retry`,
} as const;

export const API_KEY_ENDPOINTS = {
  BASE: "/api-keys",
  BY_ID: (keyId: string) => `/api-keys/${keyId}`,
} as const;

export const BOSS_HELPER_ENDPOINTS = {
  STATUS: "/integrations/boss-helper/pairing/status",
  APPROVE: "/integrations/boss-helper/pairing/approve",
  REVOKE: "/integrations/boss-helper/pairing/revoke",
} as const;

export const REQUEST_ENDPOINTS = {
  BASE: "/requests",
} as const;

export const USAGE_ENDPOINTS = {
  SUMMARY: "/usage/summary",
  SUBJECTS: "/usage/subjects",
  POLICIES: "/usage/policies",
} as const;

export const ENTITY_ENDPOINTS = {
  BASE: "/entities",
  DETAIL: (type: string, id: string) =>
    `/entities/${type}/${encodeURIComponent(id)}`,
  BY_ID: (type: string, id: string) =>
    `/entities/${type}/${encodeURIComponent(id)}`,
} as const;

export const WEBHOOK_ENDPOINTS = {
  BASE: "/webhooks",
  BY_ID: (hookId: string) => `/webhooks/${hookId}`,
  TEST: (hookId: string) => `/webhooks/${hookId}/test`,
} as const;

export const SETTINGS_ENDPOINTS = {
  WORKSPACE: "/settings/workspace",
  MEMBERS: "/settings/workspace/members",
  MEMBER: (email: string) =>
    `/settings/workspace/members/${encodeURIComponent(email)}`,
  ORGANIZATIONS: "/api/v1/orgs/organizations/",
  ORGANIZATION: (orgId: string) =>
    `/api/v1/orgs/organizations/${encodeURIComponent(orgId)}/`,
  ORG_MEMBERS: (orgId: string) =>
    `/api/v1/orgs/organizations/${encodeURIComponent(orgId)}/members/`,
  ORG_PROJECTS: (orgId: string) =>
    `/api/v1/orgs/organizations/${encodeURIComponent(orgId)}/projects/`,
  ORG_PROJECT: (orgId: string, projectId: string) =>
    `/api/v1/orgs/organizations/${encodeURIComponent(orgId)}/projects/${encodeURIComponent(projectId)}/`,
  ORG_PROJECT_MEMBERS: (orgId: string, projectId: string) =>
    `/api/v1/orgs/organizations/${encodeURIComponent(orgId)}/projects/${encodeURIComponent(projectId)}/members/`,
} as const;

export const EXPORT_ENDPOINTS = {
  BASE: "/memory-exports",
  BY_ID: (exportId: string) => `/memory-exports/${exportId}`,
} as const;

export const GRAPH_ENDPOINTS = {
  BASE: "/graph",
  STATUS: "/graph/status",
  ENTITIES: "/graph/entities",
  NEIGHBORS: (memoryId: string) => `/graph/memories/${memoryId}/neighbors`,
  SYNC: "/graph/sync",
} as const;

export const PLAYGROUND_ENDPOINTS = {
  CHAT: "/playground/chat",
} as const;
