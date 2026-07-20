// This file was modified in 2026 by YiQiao contributors. See NOTICE.

export interface Memory {
  id: string;
  memory: string;
  project_id?: string;
  user_id?: string;
  agent_id?: string;
  app_id?: string;
  run_id?: string;
  expiration_date?: string | null;
  categories?: string[];
  metadata?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface ApiKey {
  id: string;
  label: string;
  key_prefix: string;
  project_id?: string;
  created_at: string;
  last_used_at: string | null;
}

export interface ApiKeyCreateResponse {
  id: string;
  label: string;
  key: string;
  key_prefix: string;
  project_id?: string;
  created_at: string;
}

export interface ApiRequestLog {
  id: string;
  created_at: string;
  method: string;
  path: string;
  status_code: number;
  latency_ms: number;
  auth_type: string;
  project_id?: string | null;
  operation: string;
  event_type: string;
  entities: RequestEntityRef[];
  request_payload?: unknown;
  response_payload?: unknown;
  result_count?: number | null;
  has_results: boolean;
  status: "succeeded" | "failed";
}

export interface RequestEntityRef {
  type: EntityType;
  id: string;
}

export interface RequestTrendPoint {
  bucket: string;
  count: number;
}

export interface RequestLogPage {
  items: ApiRequestLog[];
  total: number;
  page: number;
  page_size: number;
  series: RequestTrendPoint[];
}

export type UsageScopeType = "organization" | "project" | "api_key" | "member";

export type UsageMetric =
  | "api_requests"
  | "memory_writes"
  | "memory_searches"
  | "stored_memories";

export type QuotaMode = "monitor" | "soft" | "hard";

export interface QuotaPolicy {
  id?: string;
  scope_type: UsageScopeType;
  scope_id: string;
  project_id: string;
  metric: UsageMetric;
  period: "minute" | "day" | "month" | "total";
  limit_value: number;
  mode: QuotaMode;
  warning_threshold: number;
  used?: number;
  percent?: number;
  updated_at?: string;
}

export interface UsageSubjects {
  organization: { id: string; name: string };
  project: { id: string; name: string };
  members: { email: string; role: string; status: string }[];
  api_keys: {
    id: string;
    label: string;
    key_prefix: string;
    created_by: string;
  }[];
  can_manage_project: boolean;
  can_manage_organization: boolean;
  current_member_email: string | null;
}

export interface UsageSummary {
  scope: {
    type: UsageScopeType;
    id: string;
    project_id: string;
    organization_id: string;
  };
  period: { days: number; start: string; end: string };
  totals: {
    stored_memories: number;
    api_requests: number;
    errors: number;
    memory_writes: number;
    memory_searches: number;
  };
  series: {
    date: string;
    api_requests: number;
    memory_writes: number;
    memory_searches: number;
  }[];
  breakdown: {
    api_keys: { id: string; label: string; requests: number }[];
    members: { id: string; email: string; requests: number }[];
  };
  effective_limits: QuotaPolicy[];
  can_manage: boolean;
  metering: { model_tokens_available: boolean; reason: string };
}

export type EntityType = "user" | "agent" | "app" | "run";

export interface Entity {
  id: string;
  type: EntityType;
  total_memories: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface EntityDetail extends Entity {
  total_requests: number;
}

export interface Webhook {
  id: string;
  name: string;
  url: string;
  events: string[];
  enabled: boolean;
  created_at: string;
  last_delivery_status: string | null;
  last_delivery_at: string | null;
  signing_secret?: string | null;
}

export interface MemoryExportJob {
  id: string;
  status: string;
  entity: Record<string, unknown>;
  started_at: string;
  completed_at: string | null;
  filters: Record<string, unknown>;
  date_range?: { start?: string; end?: string } | null;
  pydantic_schema?: Record<string, unknown> | null;
  result?: {
    exported_at: string;
    total: number;
    memories: Record<string, unknown>[];
  };
}

export interface MemoryExportPage {
  items: MemoryExportJob[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  has_next: boolean;
  has_previous: boolean;
}

export interface GraphNode {
  id: string;
  label: string;
  title?: string;
  kind?: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
  weight?: string;
}

export interface GraphStatus {
  configured: boolean;
  enabled?: boolean;
  reachable?: boolean;
  project_id?: string;
  memories?: number;
  entities?: number;
  relationships?: number;
  last_error?: string | null;
}

export interface GraphEntity {
  name: string;
  norm: string;
  type: string;
  memory_count: number;
}

export interface GraphResponse {
  configured: boolean;
  nodes: GraphNode[];
  edges: GraphEdge[];
  status?: GraphStatus;
  error?: string;
}
