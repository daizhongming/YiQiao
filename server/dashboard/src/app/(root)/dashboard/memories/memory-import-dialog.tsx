"use client";

import axios from "axios";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Archive,
  CheckCircle2,
  FileText,
  FolderOpen,
  LoaderCircle,
  Plus,
  RefreshCw,
  Trash2,
  Upload,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/use-toast";
import { getErrorMessage } from "@/lib/error-message";
import type { Language } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { api, getActiveProjectId } from "@/utils/api";
import { MEMORY_IMPORT_ENDPOINTS } from "@/utils/api-endpoints";

type EntityType = "user" | "agent" | "app" | "run";
type ImportStatus =
  | "queued"
  | "discovering"
  | "parsing"
  | "importing"
  | "syncing_graph"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "completed_with_errors"
  | "failed";

interface EntityRow {
  key: number;
  type: EntityType;
  id: string;
}

interface SelectedFile {
  key: string;
  path: string;
  file: File;
}

interface ImportError {
  source: string;
  message: string;
  type?: string;
  code?: string;
  retryable?: boolean;
  attempt?: number;
  details?: {
    root_exception_type?: string;
    operation_phase?: string;
    failure_point?: string;
    status_code?: number;
  };
}

function importErrorDiagnostic(error: ImportError) {
  const values = [
    error.code,
    error.details?.root_exception_type,
    error.details?.status_code
      ? `HTTP ${error.details.status_code}`
      : undefined,
    error.details?.failure_point ?? error.details?.operation_phase,
  ].filter((value): value is string => Boolean(value));
  return [...new Set(values)].join(" / ");
}

interface ImportMetrics {
  throughput_chunks_per_minute?: number;
  average_chunk_seconds?: number;
  p95_chunk_seconds?: number;
  failure_rate?: number;
  eta_seconds?: number;
  phase_durations_ms?: Record<string, number>;
}

interface MemoryImportJob {
  id: string;
  status: ImportStatus;
  phase?: string;
  created_at: string;
  updated_at: string;
  input_files: string[];
  entities: Record<string, string>;
  source_app: string;
  infer: boolean;
  total_input_files: number;
  discovered_files: number;
  parsed_files: number;
  skipped_files: number;
  total_conversations: number;
  total_chunks: number;
  processed_chunks: number;
  imported_chunks: number;
  skipped_chunks: number;
  failed_chunks: number;
  retried_chunks?: number;
  split_chunks?: number;
  memories_created: number;
  configured_workers?: number;
  active_workers?: number;
  current_concurrency?: number;
  graph_status?: string;
  graph_error?: string | null;
  graph_attempts?: number;
  current_file?: string | null;
  current_conversation?: string | null;
  errors: ImportError[];
  error_count?: number;
  metrics?: ImportMetrics;
  poll_after_ms?: number | null;
  source_retry_available?: boolean;
}

interface MemoryImportDialogProps {
  language: Language;
  onImported: () => void | Promise<void>;
}

const DOCUMENT_ACCEPT = ".md,.markdown,.mdx,.txt,.json,.jsonl";
const ARCHIVE_ACCEPT =
  ".zip,.tar,.tar.gz,.tgz,.tar.bz2,.tbz2,.tar.xz,.txz,application/zip,application/x-tar";
const DOCUMENT_SUFFIXES = [
  ".md",
  ".markdown",
  ".mdx",
  ".txt",
  ".json",
  ".jsonl",
];
const ARCHIVE_SUFFIXES = [
  ".zip",
  ".tar",
  ".tar.gz",
  ".tgz",
  ".tar.bz2",
  ".tbz2",
  ".tar.xz",
  ".txz",
];
const TERMINAL_STATUSES = new Set<ImportStatus>([
  "cancelled",
  "completed",
  "completed_with_errors",
  "failed",
]);
const ACTIVE_STATUSES = new Set<ImportStatus>([
  "queued",
  "discovering",
  "parsing",
  "importing",
  "syncing_graph",
  "cancelling",
]);
const MEMORY_IMPORT_JOB_STORAGE_PREFIX = "yiqiao_memory_import_job";
const RESTORE_LIST_LIMIT = 100;
const ENTITY_TYPES: EntityType[] = ["user", "agent", "app", "run"];

let nextEntityKey = 1;

function isSupportedFile(file: File) {
  const name = file.name.toLowerCase();
  return [...DOCUMENT_SUFFIXES, ...ARCHIVE_SUFFIXES].some((suffix) =>
    name.endsWith(suffix),
  );
}

function relativeFilePath(file: File) {
  const withRelativePath = file as File & { webkitRelativePath?: string };
  return withRelativePath.webkitRelativePath || file.name;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDuration(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value) || value < 0) return "--";
  if (value < 60) return `${Math.round(value)}s`;
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60);
  if (minutes < 60) return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

function formatStageDuration(value: number) {
  if (!value) return "--";
  return formatDuration(value / 1000);
}

function isPollableJob(job: MemoryImportJob) {
  return (
    ACTIVE_STATUSES.has(job.status) ||
    job.graph_status === "syncing" ||
    (job.status === "completed_with_errors" && job.graph_status === "pending")
  );
}

function importJobStorageKey() {
  if (typeof window === "undefined") return null;
  try {
    return `${MEMORY_IMPORT_JOB_STORAGE_PREFIX}:${getActiveProjectId()}`;
  } catch {
    return null;
  }
}

function getStoredImportJobId() {
  const key = importJobStorageKey();
  if (!key) return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function forgetStoredImportJob(jobId?: string) {
  const key = importJobStorageKey();
  if (!key) return;
  try {
    if (!jobId || window.localStorage.getItem(key) === jobId) {
      window.localStorage.removeItem(key);
    }
  } catch {}
}

function rememberImportJob(job: MemoryImportJob) {
  const key = importJobStorageKey();
  if (!key) return;
  try {
    if (isPollableJob(job)) {
      window.localStorage.setItem(key, job.id);
    } else {
      forgetStoredImportJob(job.id);
    }
  } catch {}
}

type ImportStage = "parsing" | "extracting" | "storing" | "graph";
type StageState = "waiting" | "active" | "complete" | "failed";

function stageState(job: MemoryImportJob, stage: ImportStage): StageState {
  const parsing =
    job.status === "queued" ||
    job.status === "discovering" ||
    job.status === "parsing" ||
    (job.status === "cancelling" && job.phase === "parsing");
  const graphSyncing =
    job.status === "syncing_graph" ||
    job.graph_status === "syncing" ||
    job.phase === "graph_sync";
  const importing =
    job.status === "importing" ||
    (job.status === "cancelling" && !parsing && !graphSyncing);
  const terminal = TERMINAL_STATUSES.has(job.status);
  const cancelled = job.status === "cancelled";
  const failed = job.status === "failed";

  if (stage === "parsing") {
    if (parsing) return "active";
    if (
      failed &&
      (!job.discovered_files || job.parsed_files < job.discovered_files)
    ) {
      return "failed";
    }
    if (
      cancelled &&
      (!job.discovered_files || job.parsed_files < job.discovered_files)
    ) {
      return "waiting";
    }
    return "complete";
  }
  if (stage === "extracting" || stage === "storing") {
    if (parsing) return "waiting";
    if (importing) return "active";
    if (failed) {
      return job.total_chunks > 0 && job.processed_chunks >= job.total_chunks
        ? "complete"
        : "failed";
    }
    if (cancelled) {
      return job.total_chunks > 0 && job.processed_chunks >= job.total_chunks
        ? "complete"
        : "waiting";
    }
    return graphSyncing || terminal ? "complete" : "waiting";
  }
  if (job.graph_status === "failed" || job.graph_error) return "failed";
  if (graphSyncing) return "active";
  if (
    terminal &&
    (!job.graph_status ||
      ["completed", "synced", "disabled", "skipped"].includes(job.graph_status))
  ) {
    return "complete";
  }
  return "waiting";
}

function statusProgress(job: MemoryImportJob) {
  if (job.graph_status === "syncing" || job.phase === "graph_sync") return 96;
  if (job.status === "queued" || job.status === "discovering") return 4;
  if (job.status === "parsing") {
    return job.discovered_files
      ? Math.max(8, Math.round((job.parsed_files / job.discovered_files) * 35))
      : 8;
  }
  if (job.status === "importing" || job.status === "cancelling") {
    return job.total_chunks
      ? Math.min(
          92,
          35 + Math.round((job.processed_chunks / job.total_chunks) * 57),
        )
      : 35;
  }
  if (job.status === "syncing_graph") return 96;
  if (job.status === "completed" || job.status === "completed_with_errors") {
    return 100;
  }
  return job.processed_chunks && job.total_chunks
    ? Math.min(
        92,
        35 + Math.round((job.processed_chunks / job.total_chunks) * 57),
      )
    : 0;
}

export function MemoryImportDialog({
  language,
  onImported,
}: MemoryImportDialogProps) {
  const isZh = language === "zh";
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<SelectedFile[]>([]);
  const [entities, setEntities] = useState<EntityRow[]>([
    { key: nextEntityKey++, type: "user", id: "me" },
  ]);
  const [sourceApp, setSourceApp] = useState("auto");
  const [redactSecrets, setRedactSecrets] = useState(true);
  const [skipDuplicates, setSkipDuplicates] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [restoringJob, setRestoringJob] = useState(false);
  const [retryingChunks, setRetryingChunks] = useState(false);
  const [retryingGraph, setRetryingGraph] = useState(false);
  const [job, setJob] = useState<MemoryImportJob | null>(null);
  const documentInputRef = useRef<HTMLInputElement>(null);
  const archiveInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const notifiedJobRef = useRef<string | null>(null);
  const pollErrorNotifiedRef = useRef<string | null>(null);
  const restoreErrorNotifiedRef = useRef(false);
  const suppressRestoreRef = useRef(false);
  const jobId = job?.id;
  const jobStatus = job?.status;
  const jobGraphStatus = job?.graph_status;
  const updateJob = useCallback((nextJob: MemoryImportJob) => {
    rememberImportJob(nextJob);
    setJob(nextJob);
  }, []);

  const entityLabels: Record<EntityType, string> = {
    user: isZh ? "用户" : "User",
    agent: isZh ? "智能体" : "Agent",
    app: isZh ? "应用" : "App",
    run: isZh ? "运行" : "Run",
  };

  const addFiles = useCallback(
    (incoming: File[]) => {
      const supported = incoming.filter(isSupportedFile);
      const ignored = incoming.length - supported.length;
      setFiles((current) => {
        const keys = new Set(current.map((item) => item.key));
        const additions = supported
          .map((file) => {
            const path = relativeFilePath(file);
            return {
              file,
              path,
              key: `${path}:${file.size}:${file.lastModified}`,
            };
          })
          .filter((item) => !keys.has(item.key));
        return [...current, ...additions];
      });
      if (ignored) {
        toast({
          title: isZh
            ? `已忽略 ${ignored} 个不支持的文件`
            : `Ignored ${ignored} unsupported files`,
        });
      }
    },
    [isZh],
  );

  const handleInput = (event: React.ChangeEvent<HTMLInputElement>) => {
    addFiles(Array.from(event.target.files ?? []));
    event.target.value = "";
  };

  const notifyCompletion = useCallback(
    (nextJob: MemoryImportJob) => {
      if (
        !TERMINAL_STATUSES.has(nextJob.status) ||
        notifiedJobRef.current === nextJob.id
      ) {
        return;
      }
      notifiedJobRef.current = nextJob.id;
      if (nextJob.status === "completed") {
        toast({
          title: isZh ? "记忆导入完成" : "Memory import completed",
          description: isZh
            ? `已写入 ${nextJob.memories_created} 条记忆`
            : `${nextJob.memories_created} memories created`,
          variant: "success",
        });
        void onImported();
      } else if (nextJob.status === "completed_with_errors") {
        const graphFailed =
          nextJob.graph_status === "failed" || Boolean(nextJob.graph_error);
        toast({
          title: isZh
            ? "导入完成，但有部分错误"
            : "Import completed with errors",
          description: graphFailed
            ? isZh
              ? "记忆已写入，但图谱同步失败"
              : "Memories were stored, but graph sync failed"
            : isZh
              ? `${nextJob.imported_chunks} 个分块成功，${nextJob.failed_chunks} 个失败`
              : `${nextJob.imported_chunks} chunks succeeded, ${nextJob.failed_chunks} failed`,
          variant: "destructive",
        });
        void onImported();
      } else if (nextJob.status === "failed") {
        toast({
          title: isZh ? "导入失败" : "Import failed",
          description: nextJob.errors[0]?.message,
          variant: "destructive",
        });
      }
    },
    [isZh, onImported],
  );

  useEffect(() => {
    if (job) notifyCompletion(job);
  }, [job, notifyCompletion]);

  useEffect(() => {
    const pollable =
      Boolean(jobStatus && ACTIVE_STATUSES.has(jobStatus)) ||
      jobGraphStatus === "syncing" ||
      (jobStatus === "completed_with_errors" && jobGraphStatus === "pending");
    if (!open || !jobId || !pollable) return;

    let active = true;
    let polling = false;
    let timer: number | null = null;
    let controller: AbortController | null = null;
    let consecutiveErrors = 0;

    const clearTimer = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };

    const schedule = (delay: number) => {
      clearTimer();
      if (!active || document.hidden) return;
      timer = window.setTimeout(() => void poll(), delay);
    };

    const poll = async () => {
      if (!active || polling || document.hidden) return;
      polling = true;
      controller = new AbortController();
      try {
        const response = await api.get(MEMORY_IMPORT_ENDPOINTS.BY_ID(jobId), {
          signal: controller.signal,
        });
        if (!active) return;
        const nextJob = response.data as MemoryImportJob;
        consecutiveErrors = 0;
        updateJob(nextJob);
        notifyCompletion(nextJob);
        if (isPollableJob(nextJob)) {
          schedule(Math.max(3000, Number(nextJob.poll_after_ms) || 3000));
        }
      } catch (error) {
        if (!active || document.hidden) return;
        consecutiveErrors += 1;
        if (pollErrorNotifiedRef.current !== jobId) {
          pollErrorNotifiedRef.current = jobId;
          toast({
            title: isZh ? "无法读取导入进度" : "Failed to read import progress",
            description: getErrorMessage(error),
            variant: "destructive",
          });
        }
        schedule(Math.min(60_000, 3000 * 2 ** Math.min(consecutiveErrors, 4)));
      } finally {
        polling = false;
        controller = null;
      }
    };

    const handleVisibilityChange = () => {
      if (document.hidden) {
        clearTimer();
        controller?.abort();
      } else {
        schedule(0);
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    schedule(0);
    return () => {
      active = false;
      clearTimer();
      controller?.abort();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [
    isZh,
    jobGraphStatus,
    jobId,
    jobStatus,
    notifyCompletion,
    open,
    updateJob,
  ]);

  useEffect(() => {
    if (!open || job || suppressRestoreRef.current) return;
    let active = true;
    let timer: number | null = null;
    let controller: AbortController | null = null;
    let consecutiveErrors = 0;
    let settled = false;
    setRestoringJob(true);

    const clearTimer = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };

    const schedule = (delay: number) => {
      clearTimer();
      if (!active || settled || document.hidden) return;
      timer = window.setTimeout(() => void restore(), delay);
    };

    const acceptRestoredJob = (restoredJob: MemoryImportJob) => {
      consecutiveErrors = 0;
      restoreErrorNotifiedRef.current = false;
      notifiedJobRef.current = TERMINAL_STATUSES.has(restoredJob.status)
        ? restoredJob.id
        : null;
      pollErrorNotifiedRef.current = null;
      settled = true;
      updateJob(restoredJob);
    };

    const restore = async () => {
      if (!active || settled || document.hidden) return;
      controller = new AbortController();
      try {
        let storedTerminalJob: MemoryImportJob | undefined;
        const storedJobId = getStoredImportJobId();
        if (storedJobId) {
          try {
            const storedResponse = await api.get(
              MEMORY_IMPORT_ENDPOINTS.BY_ID(storedJobId),
              { signal: controller.signal },
            );
            if (!active) return;
            const storedJob = storedResponse.data as MemoryImportJob;
            if (isPollableJob(storedJob)) {
              acceptRestoredJob(storedJob);
              return;
            }
            forgetStoredImportJob(storedJobId);
            if (TERMINAL_STATUSES.has(storedJob.status)) {
              storedTerminalJob = storedJob;
            }
          } catch (error) {
            if (!active || document.hidden) return;
            if (
              axios.isAxiosError(error) &&
              [403, 404].includes(error.response?.status ?? 0)
            ) {
              forgetStoredImportJob(storedJobId);
            } else {
              throw error;
            }
          }
        }

        const response = await api.get(MEMORY_IMPORT_ENDPOINTS.BASE, {
          params: { limit: RESTORE_LIST_LIMIT },
          signal: controller.signal,
        });
        if (!active) return;
        const results = (response.data?.results ?? []) as MemoryImportJob[];
        const restoredJob =
          results.find(isPollableJob) ??
          storedTerminalJob ??
          results.find((item) => TERMINAL_STATUSES.has(item.status));
        if (restoredJob) {
          acceptRestoredJob(restoredJob);
          return;
        }
        consecutiveErrors = 0;
        restoreErrorNotifiedRef.current = false;
        settled = true;
        setRestoringJob(false);
      } catch (error) {
        if (!active || document.hidden) return;
        consecutiveErrors += 1;
        if (!restoreErrorNotifiedRef.current) {
          restoreErrorNotifiedRef.current = true;
          toast({
            title: isZh
              ? "无法恢复导入进度"
              : "Failed to restore import progress",
            description: getErrorMessage(error),
            variant: "destructive",
          });
        }
        schedule(Math.min(60_000, 3000 * 2 ** Math.min(consecutiveErrors, 4)));
      } finally {
        controller = null;
      }
    };

    const handleVisibilityChange = () => {
      if (document.hidden) {
        clearTimer();
        controller?.abort();
      } else {
        schedule(0);
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    schedule(0);
    return () => {
      active = false;
      clearTimer();
      controller?.abort();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [isZh, job, open, updateJob]);

  const totalBytes = useMemo(
    () => files.reduce((total, item) => total + item.file.size, 0),
    [files],
  );
  const entityTypes = useMemo(
    () => new Set(entities.map((entity) => entity.type)),
    [entities],
  );
  const entitiesValid =
    entities.length > 0 &&
    entities.every((entity) => entity.id.trim()) &&
    entityTypes.size === entities.length;

  const updateEntity = (key: number, patch: Partial<EntityRow>) => {
    setEntities((current) =>
      current.map((entity) =>
        entity.key === key ? { ...entity, ...patch } : entity,
      ),
    );
  };

  const addEntity = () => {
    const available = ENTITY_TYPES.find((type) => !entityTypes.has(type));
    if (!available) return;
    setEntities((current) => [
      ...current,
      { key: nextEntityKey++, type: available, id: "" },
    ]);
  };

  const startImport = async () => {
    if (!files.length || !entitiesValid) {
      toast({
        title: isZh
          ? "请先选择文件并填写实体 ID"
          : "Select files and complete the entity IDs",
        variant: "destructive",
      });
      return;
    }
    const form = new FormData();
    files.forEach((item) => form.append("files", item.file, item.path));
    form.append(
      "options",
      JSON.stringify({
        entities: entities.map((entity) => ({
          type: entity.type,
          id: entity.id.trim(),
        })),
        source_app: sourceApp,
        infer: true,
        redact_secrets: redactSecrets,
        skip_duplicates: skipDuplicates,
      }),
    );
    setSubmitting(true);
    try {
      const response = await api.post(MEMORY_IMPORT_ENDPOINTS.BASE, form);
      const nextJob = response.data as MemoryImportJob;
      notifiedJobRef.current = null;
      pollErrorNotifiedRef.current = null;
      suppressRestoreRef.current = false;
      updateJob(nextJob);
    } catch (error) {
      toast({
        title: isZh ? "无法开始导入" : "Failed to start import",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setSubmitting(false);
    }
  };

  const cancelImport = async () => {
    if (!job) return;
    try {
      const response = await api.post(MEMORY_IMPORT_ENDPOINTS.CANCEL(job.id));
      updateJob(response.data as MemoryImportJob);
    } catch (error) {
      toast({
        title: isZh ? "无法取消导入" : "Failed to cancel import",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const retryFailedChunks = async () => {
    if (!job || retryingChunks) return;
    setRetryingChunks(true);
    try {
      const response = await api.post(MEMORY_IMPORT_ENDPOINTS.RETRY(job.id));
      notifiedJobRef.current = null;
      pollErrorNotifiedRef.current = null;
      updateJob(response.data as MemoryImportJob);
    } catch (error) {
      toast({
        title: isZh ? "无法重试失败分块" : "Failed to retry chunks",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setRetryingChunks(false);
    }
  };

  const retryGraphSync = async () => {
    if (!job || retryingGraph) return;
    setRetryingGraph(true);
    try {
      const response = await api.post(
        MEMORY_IMPORT_ENDPOINTS.GRAPH_RETRY(job.id),
      );
      notifiedJobRef.current = null;
      pollErrorNotifiedRef.current = null;
      updateJob(response.data as MemoryImportJob);
    } catch (error) {
      toast({
        title: isZh ? "无法重试图谱同步" : "Failed to retry graph sync",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setRetryingGraph(false);
    }
  };

  const reset = () => {
    suppressRestoreRef.current = true;
    setJob(null);
    setRestoringJob(false);
    setFiles([]);
    notifiedJobRef.current = null;
    pollErrorNotifiedRef.current = null;
  };

  const progress = job ? statusProgress(job) : 0;
  const sourceRetryAvailable = Boolean(job?.source_retry_available);
  const resumingCancelledImport =
    sourceRetryAvailable && job?.status === "cancelled";
  const attemptIssueCount = job
    ? Math.max(job.error_count ?? 0, job.errors.length)
    : 0;
  const hasRecoveredAttemptIssues = Boolean(
    job &&
    job.status === "completed" &&
    job.failed_chunks === 0 &&
    attemptIssueCount > 0,
  );
  const statusLabels: Record<ImportStatus, string> = {
    queued: isZh ? "等待处理" : "Queued",
    discovering: isZh ? "正在读取文件" : "Reading files",
    parsing: isZh ? "正在解析对话" : "Parsing conversations",
    importing: isZh ? "正在提取记忆" : "Extracting memories",
    syncing_graph: isZh ? "正在同步图谱" : "Syncing graph",
    cancelling: isZh ? "正在取消" : "Cancelling",
    cancelled: isZh ? "已取消" : "Cancelled",
    completed: isZh ? "导入完成" : "Import completed",
    completed_with_errors: isZh
      ? "导入完成，部分失败"
      : "Completed with errors",
    failed: isZh ? "导入失败" : "Import failed",
  };

  const phaseDurations = job?.metrics?.phase_durations_ms ?? {};
  const durationFor = (...keys: string[]) =>
    keys.reduce((total, key) => total + (phaseDurations[key] ?? 0), 0);
  const stages = job
    ? [
        {
          key: "parsing" as const,
          label: isZh ? "解析" : "Parse",
          duration: durationFor("parsing", "deduplication"),
        },
        {
          key: "extracting" as const,
          label: isZh ? "提取" : "Extract",
          duration: durationFor("llm", "entity_processing"),
        },
        {
          key: "storing" as const,
          label: isZh ? "存储" : "Store",
          duration: durationFor("embedding", "pgvector"),
        },
        {
          key: "graph" as const,
          label: isZh ? "图谱" : "Graph",
          duration: durationFor("neo4j"),
        },
      ].map((stage) => ({
        ...stage,
        state: stageState(job, stage.key),
      }))
    : [];
  const graphStatusLabels: Record<string, string> = {
    pending: isZh ? "等待" : "Pending",
    syncing: isZh ? "同步中" : "Syncing",
    completed: isZh ? "完成" : "Completed",
    synced: isZh ? "完成" : "Synced",
    failed: isZh ? "失败" : "Failed",
    disabled: isZh ? "未启用" : "Disabled",
    skipped: isZh ? "已跳过" : "Skipped",
  };
  const effectiveGraphStatus =
    job?.graph_status ??
    (job?.phase === "graph_sync"
      ? "syncing"
      : job && TERMINAL_STATUSES.has(job.status)
        ? "skipped"
        : "pending");
  const graphFailed = Boolean(
    job && (job.graph_status === "failed" || job.graph_error),
  );
  const graphDuration = durationFor("neo4j");
  const graphStageText = `${graphStatusLabels[effectiveGraphStatus] ?? effectiveGraphStatus}${
    graphDuration ? ` · ${formatStageDuration(graphDuration)}` : ""
  }`;
  const performanceStats = job
    ? [
        {
          label: isZh ? "活跃/当前/配置" : "Active/current/set",
          value: `${job.active_workers ?? 0}/${job.current_concurrency ?? 1}/${job.configured_workers ?? 1}`,
        },
        {
          label: isZh ? "速度" : "Speed",
          value: `${(job.metrics?.throughput_chunks_per_minute ?? 0).toFixed(1)}/min`,
        },
        {
          label: isZh ? "平均/P95" : "Average/P95",
          value: `${formatDuration(job.metrics?.average_chunk_seconds)} / ${formatDuration(job.metrics?.p95_chunk_seconds)}`,
        },
        {
          label: isZh ? "失败率" : "Failure rate",
          value: `${((job.metrics?.failure_rate ?? 0) * 100).toFixed(1)}%`,
        },
        {
          label: isZh ? "预计剩余" : "ETA",
          value: formatDuration(job.metrics?.eta_seconds),
        },
      ]
    : [];
  const resultStats = job
    ? [
        [isZh ? "成功" : "Succeeded", job.imported_chunks],
        [isZh ? "跳过" : "Skipped", job.skipped_chunks],
        [isZh ? "重试" : "Retried", job.retried_chunks ?? 0],
        [isZh ? "失败" : "Failed", job.failed_chunks],
        [isZh ? "拆分" : "Split", job.split_chunks ?? 0],
        [isZh ? "记忆" : "Memories", job.memories_created],
      ]
    : [];

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (nextOpen) {
          suppressRestoreRef.current = false;
          if (!job) setRestoringJob(true);
        }
        setOpen(nextOpen);
      }}
    >
      <DialogTrigger asChild>
        <Button variant="primary" className="h-9 gap-2 px-3 text-xs">
          <Upload className="size-3.5" />
          {isZh ? "导入记忆" : "Import"}
        </Button>
      </DialogTrigger>
      <DialogContent className="flex max-h-[min(90vh,760px)] w-[calc(100vw-24px)] max-w-[680px] flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-memBorder-primary px-5 py-4 pr-12 text-left">
          <DialogTitle className="text-base">
            {isZh ? "导入聊天记忆" : "Import chat memories"}
          </DialogTitle>
          <DialogDescription className="sr-only">
            {isZh
              ? "从聊天记录文件导入记忆"
              : "Import memories from chat history files"}
          </DialogDescription>
        </DialogHeader>

        {!job && restoringJob ? (
          <div className="flex min-h-52 flex-1 items-center justify-center gap-2 px-5 py-8 text-sm text-onSurface-default-secondary">
            <LoaderCircle className="size-4 animate-spin" />
            {isZh ? "正在恢复导入进度" : "Restoring import progress"}
          </div>
        ) : !job ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            <input
              ref={documentInputRef}
              className="hidden"
              type="file"
              accept={DOCUMENT_ACCEPT}
              multiple
              onChange={handleInput}
            />
            <input
              ref={archiveInputRef}
              className="hidden"
              type="file"
              accept={ARCHIVE_ACCEPT}
              multiple
              onChange={handleInput}
            />
            <input
              ref={(node) => {
                folderInputRef.current = node;
                node?.setAttribute("webkitdirectory", "");
                node?.setAttribute("directory", "");
              }}
              className="hidden"
              type="file"
              multiple
              onChange={handleInput}
            />

            <div
              className="rounded-md border border-dashed border-memBorder-primary p-3"
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                addFiles(Array.from(event.dataTransfer.files));
              }}
            >
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                <Button
                  type="button"
                  variant="outline"
                  className="h-10 gap-2"
                  onClick={() => documentInputRef.current?.click()}
                >
                  <FileText className="size-4" />
                  {isZh ? "选择文件" : "Files"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  className="h-10 gap-2"
                  onClick={() => folderInputRef.current?.click()}
                >
                  <FolderOpen className="size-4" />
                  {isZh ? "选择文件夹" : "Folder"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  className="h-10 gap-2"
                  onClick={() => archiveInputRef.current?.click()}
                >
                  <Archive className="size-4" />
                  {isZh ? "选择压缩包" : "Archive"}
                </Button>
              </div>
            </div>

            {files.length > 0 && (
              <div className="mt-3 rounded-md border border-memBorder-primary">
                <div className="flex items-center justify-between border-b border-memBorder-primary px-3 py-2">
                  <span className="text-xs font-medium">
                    {isZh ? `${files.length} 个文件` : `${files.length} files`}
                  </span>
                  <div className="flex items-center gap-2 text-[11px] text-onSurface-default-tertiary">
                    <span>{formatBytes(totalBytes)}</span>
                    <button
                      type="button"
                      className="text-onSurface-default-secondary hover:text-onSurface-default-primary"
                      onClick={() => setFiles([])}
                    >
                      {isZh ? "清空" : "Clear"}
                    </button>
                  </div>
                </div>
                <div className="max-h-36 overflow-y-auto py-1">
                  {files.slice(0, 20).map((item) => (
                    <div
                      key={item.key}
                      className="flex h-8 min-w-0 items-center gap-2 px-3 text-xs"
                    >
                      {ARCHIVE_SUFFIXES.some((suffix) =>
                        item.path.toLowerCase().endsWith(suffix),
                      ) ? (
                        <Archive className="size-3.5 shrink-0 text-onSurface-default-tertiary" />
                      ) : (
                        <FileText className="size-3.5 shrink-0 text-onSurface-default-tertiary" />
                      )}
                      <span
                        className="min-w-0 flex-1 truncate"
                        title={item.path}
                      >
                        {item.path}
                      </span>
                      <span className="shrink-0 text-[10px] text-onSurface-default-tertiary">
                        {formatBytes(item.file.size)}
                      </span>
                      <button
                        type="button"
                        className="flex size-6 shrink-0 items-center justify-center rounded hover:bg-surface-default-tertiary"
                        aria-label={
                          isZh ? `移除 ${item.path}` : `Remove ${item.path}`
                        }
                        title={isZh ? "移除" : "Remove"}
                        onClick={() =>
                          setFiles((current) =>
                            current.filter((file) => file.key !== item.key),
                          )
                        }
                      >
                        <Trash2 className="size-3.5" />
                      </button>
                    </div>
                  ))}
                  {files.length > 20 && (
                    <div className="px-3 py-1.5 text-[11px] text-onSurface-default-tertiary">
                      {isZh
                        ? `另有 ${files.length - 20} 个文件`
                        : `${files.length - 20} more files`}
                    </div>
                  )}
                </div>
              </div>
            )}

            <div className="mt-5 space-y-3">
              <div className="flex items-center justify-between">
                <Label>{isZh ? "归属实体" : "Entities"}</Label>
                <Button
                  type="button"
                  variant="subtle"
                  size="sm"
                  className="h-7 gap-1 px-2"
                  disabled={entities.length >= ENTITY_TYPES.length}
                  onClick={addEntity}
                >
                  <Plus className="size-3.5" />
                  {isZh ? "添加" : "Add"}
                </Button>
              </div>
              <div className="space-y-2">
                {entities.map((entity) => (
                  <div
                    key={entity.key}
                    className="flex min-w-0 items-center gap-2"
                  >
                    <Select
                      value={entity.type}
                      onValueChange={(value) =>
                        updateEntity(entity.key, { type: value as EntityType })
                      }
                    >
                      <SelectTrigger className="h-9 w-[112px] shrink-0 text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {ENTITY_TYPES.map((type) => (
                          <SelectItem
                            key={type}
                            value={type}
                            disabled={
                              type !== entity.type && entityTypes.has(type)
                            }
                          >
                            {entityLabels[type]}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Input
                      value={entity.id}
                      className="h-9 min-w-0 flex-1 text-xs"
                      aria-label={`${entityLabels[entity.type]} ID`}
                      placeholder={`${entityLabels[entity.type]} ID`}
                      onChange={(event) =>
                        updateEntity(entity.key, { id: event.target.value })
                      }
                    />
                    <button
                      type="button"
                      className="flex size-9 shrink-0 items-center justify-center rounded-md border border-memBorder-primary hover:bg-surface-default-tertiary disabled:cursor-not-allowed disabled:opacity-40"
                      disabled={entities.length === 1}
                      aria-label={isZh ? "删除实体" : "Remove entity"}
                      title={isZh ? "删除实体" : "Remove entity"}
                      onClick={() =>
                        setEntities((current) =>
                          current.filter((item) => item.key !== entity.key),
                        )
                      }
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>

            <div className="mt-5 grid gap-4 border-t border-memBorder-primary pt-4 sm:grid-cols-[180px_1fr]">
              <div className="space-y-2">
                <Label>{isZh ? "来源" : "Source"}</Label>
                <Select value={sourceApp} onValueChange={setSourceApp}>
                  <SelectTrigger className="h-9 w-full text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">
                      {isZh ? "自动识别" : "Auto detect"}
                    </SelectItem>
                    <SelectItem value="chatgpt">ChatGPT</SelectItem>
                    <SelectItem value="doubao">
                      {isZh ? "豆包" : "Doubao"}
                    </SelectItem>
                    <SelectItem value="generic">
                      {isZh ? "通用" : "Generic"}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {[
                  {
                    checked: redactSecrets,
                    setChecked: setRedactSecrets,
                    label: isZh ? "隐藏密钥" : "Redact secrets",
                  },
                  {
                    checked: skipDuplicates,
                    setChecked: setSkipDuplicates,
                    label: isZh ? "跳过重复" : "Skip duplicates",
                  },
                ].map((option) => (
                  <div
                    key={option.label}
                    className="flex items-center justify-between gap-2 sm:flex-col sm:items-start"
                  >
                    <Label className="text-xs font-normal leading-5">
                      {option.label}
                    </Label>
                    <Switch
                      checked={option.checked}
                      onCheckedChange={option.setChecked}
                    />
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
            <div className="flex items-start gap-3">
              {isPollableJob(job) ? (
                <LoaderCircle className="mt-0.5 size-5 shrink-0 animate-spin text-onSurface-default-secondary" />
              ) : job.status === "completed" ? (
                <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-emerald-600" />
              ) : job.status === "cancelled" ? (
                <XCircle className="mt-0.5 size-5 shrink-0 text-onSurface-default-tertiary" />
              ) : (
                <AlertCircle className="mt-0.5 size-5 shrink-0 text-onSurface-danger-primary" />
              )}
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold">
                  {job.graph_status === "syncing"
                    ? statusLabels.syncing_graph
                    : statusLabels[job.status]}
                </p>
                {(job.current_conversation || job.current_file) && (
                  <p
                    className="mt-1 truncate text-xs text-onSurface-default-tertiary"
                    title={
                      job.current_conversation || job.current_file || undefined
                    }
                  >
                    {job.current_conversation || job.current_file}
                  </p>
                )}
              </div>
              <span className="shrink-0 font-mono text-xs text-onSurface-default-tertiary">
                {progress}%
              </span>
            </div>

            <div className="mt-4 h-2 overflow-hidden rounded-full bg-surface-default-tertiary">
              <div
                className={cn(
                  "h-full rounded-full transition-[width] duration-500",
                  job.status === "failed" ||
                    job.status === "completed_with_errors"
                    ? "bg-onSurface-danger-primary"
                    : "bg-onSurface-default-primary",
                  (job.status === "queued" || job.status === "discovering") &&
                    "animate-pulse",
                )}
                style={{ width: `${progress}%` }}
              />
            </div>

            <div className="mt-5 grid grid-cols-4 divide-x divide-memBorder-primary border-y border-memBorder-primary">
              {stages.map((stage) => (
                <div key={stage.key} className="min-w-0 px-2 py-3 sm:px-3">
                  <div className="flex items-center gap-1.5">
                    {stage.state === "active" ? (
                      <LoaderCircle className="size-3.5 shrink-0 animate-spin text-onSurface-default-primary" />
                    ) : stage.state === "complete" ? (
                      <CheckCircle2 className="size-3.5 shrink-0 text-emerald-600" />
                    ) : stage.state === "failed" ? (
                      <AlertCircle className="size-3.5 shrink-0 text-onSurface-danger-primary" />
                    ) : (
                      <span className="size-2.5 shrink-0 rounded-full border border-memBorder-primary" />
                    )}
                    <p className="truncate text-xs font-medium">
                      {stage.label}
                    </p>
                  </div>
                  <p
                    className={cn(
                      "mt-1 truncate pl-5 font-mono text-[10px] text-onSurface-default-tertiary",
                      stage.state === "failed" &&
                        "text-onSurface-danger-primary",
                    )}
                    title={stage.key === "graph" ? graphStageText : undefined}
                  >
                    {stage.key === "graph"
                      ? graphStageText
                      : formatStageDuration(stage.duration)}
                  </p>
                </div>
              ))}
            </div>

            <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-onSurface-default-tertiary">
              <span>
                {isZh ? "文件" : "Files"} {job.parsed_files}/
                {job.discovered_files}
              </span>
              <span>
                {isZh ? "对话" : "Chats"} {job.total_conversations}
              </span>
              <span>
                {isZh ? "分块" : "Chunks"} {job.processed_chunks}/
                {job.total_chunks}
              </span>
              {job.skipped_files > 0 && (
                <span>
                  {isZh ? "跳过文件" : "Files skipped"} {job.skipped_files}
                </span>
              )}
              {attemptIssueCount > 0 && (
                <span
                  className={cn(
                    hasRecoveredAttemptIssues
                      ? "text-amber-700 dark:text-amber-300"
                      : "text-onSurface-danger-primary",
                  )}
                >
                  {hasRecoveredAttemptIssues
                    ? isZh
                      ? "已恢复异常"
                      : "Recovered issues"
                    : isZh
                      ? "错误"
                      : "Errors"}{" "}
                  {attemptIssueCount}
                </span>
              )}
            </div>

            <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-5">
              {performanceStats.map((stat) => (
                <div key={stat.label} className="min-w-0">
                  <p className="truncate text-[10px] uppercase text-onSurface-default-tertiary">
                    {stat.label}
                  </p>
                  <p
                    className="mt-1 truncate font-mono text-xs font-semibold"
                    title={stat.value}
                  >
                    {stat.value}
                  </p>
                </div>
              ))}
            </div>

            <div className="mt-4 grid grid-cols-3 border-y border-memBorder-primary sm:grid-cols-6">
              {resultStats.map(([label, value], index) => (
                <div
                  key={String(label)}
                  className={cn(
                    "min-w-0 px-2 py-2.5",
                    index % 3 !== 2 &&
                      "border-r border-memBorder-primary sm:border-r-0",
                    index > 2 &&
                      "border-t border-memBorder-primary sm:border-t-0",
                    index < resultStats.length - 1 &&
                      "sm:border-r sm:border-memBorder-primary",
                  )}
                >
                  <p className="truncate text-[10px] text-onSurface-default-tertiary">
                    {label}
                  </p>
                  <p
                    className={cn(
                      "mt-0.5 font-mono text-sm font-semibold",
                      label === (isZh ? "失败" : "Failed") &&
                        Number(value) > 0 &&
                        "text-onSurface-danger-primary",
                    )}
                  >
                    {value}
                  </p>
                </div>
              ))}
            </div>

            {(sourceRetryAvailable || graphFailed) && (
              <div className="mt-4 divide-y divide-memBorder-primary border-y border-memBorder-primary">
                {sourceRetryAvailable && (
                  <div className="flex items-center justify-between gap-3 py-3">
                    <div className="min-w-0">
                      <p
                        className={cn(
                          "text-xs font-medium",
                          job.failed_chunks > 0
                            ? "text-onSurface-danger-primary"
                            : "text-onSurface-default-primary",
                        )}
                      >
                        {resumingCancelledImport
                          ? isZh
                            ? "导入已取消，仍有内容待处理"
                            : "Import cancelled with content remaining"
                          : isZh
                            ? `${job.failed_chunks} 个分块失败`
                            : `${job.failed_chunks} failed chunks`}
                      </p>
                      <p className="mt-0.5 text-[11px] text-onSurface-default-tertiary">
                        {resumingCancelledImport
                          ? isZh
                            ? `已处理 ${job.processed_chunks} / ${job.total_chunks} 个分块`
                            : `${job.processed_chunks} / ${job.total_chunks} chunks processed`
                          : isZh
                            ? `已重试 ${job.retried_chunks ?? 0} 次，拆分 ${job.split_chunks ?? 0} 次`
                            : `${job.retried_chunks ?? 0} retries, ${job.split_chunks ?? 0} splits`}
                      </p>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-8 shrink-0 gap-1.5 px-2.5 text-xs"
                      disabled={retryingChunks || isPollableJob(job)}
                      onClick={() => void retryFailedChunks()}
                    >
                      <RefreshCw
                        className={cn(
                          "size-3.5",
                          retryingChunks && "animate-spin",
                        )}
                      />
                      {resumingCancelledImport
                        ? isZh
                          ? "继续导入"
                          : "Resume import"
                        : isZh
                          ? "重试失败分块"
                          : "Retry failed chunks"}
                    </Button>
                  </div>
                )}
                {graphFailed && (
                  <div className="flex items-start justify-between gap-3 py-3">
                    <div className="min-w-0">
                      <p className="text-xs font-medium text-onSurface-danger-primary">
                        {isZh ? "图谱同步失败" : "Graph sync failed"}
                      </p>
                      {job.graph_error && (
                        <p className="mt-0.5 break-words text-[11px] text-onSurface-default-tertiary">
                          {job.graph_error}
                        </p>
                      )}
                      <p className="mt-1 text-[10px] text-onSurface-default-tertiary">
                        {isZh ? "尝试次数" : "Attempts"}:{" "}
                        {job.graph_attempts ?? 0}
                      </p>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-8 shrink-0 gap-1.5 px-2.5 text-xs"
                      disabled={retryingGraph || isPollableJob(job)}
                      onClick={() => void retryGraphSync()}
                    >
                      <RefreshCw
                        className={cn(
                          "size-3.5",
                          retryingGraph && "animate-spin",
                        )}
                      />
                      {isZh ? "重试图谱" : "Retry graph"}
                    </Button>
                  </div>
                )}
              </div>
            )}

            {job.errors.length > 0 && (
              <div className="mt-5">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <p
                    className={cn(
                      "text-xs font-medium",
                      hasRecoveredAttemptIssues &&
                        "text-amber-700 dark:text-amber-300",
                    )}
                  >
                    {hasRecoveredAttemptIssues
                      ? isZh
                        ? "已恢复的尝试异常"
                        : "Recovered attempt issues"
                      : isZh
                        ? "错误详情"
                        : "Errors"}
                  </p>
                  <span className="font-mono text-[10px] text-onSurface-default-tertiary">
                    {attemptIssueCount}
                  </span>
                </div>
                <div className="max-h-44 overflow-y-auto rounded-md border border-memBorder-primary">
                  {job.errors.slice(0, 20).map((error, index) => (
                    <div
                      key={`${error.source}-${index}`}
                      className="border-b border-memBorder-primary px-3 py-2 text-xs last:border-b-0"
                    >
                      <p className="truncate font-medium" title={error.source}>
                        {error.source}
                      </p>
                      <p className="mt-0.5 break-words text-onSurface-default-tertiary">
                        {error.message}
                      </p>
                      {(error.type || error.attempt) && (
                        <p className="mt-1 text-[10px] text-onSurface-default-tertiary">
                          {[error.type, error.attempt && `#${error.attempt}`]
                            .filter(Boolean)
                            .join(" · ")}
                        </p>
                      )}
                      {importErrorDiagnostic(error) && (
                        <p className="mt-1 break-words font-mono text-[10px] text-onSurface-default-tertiary">
                          {importErrorDiagnostic(error)}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <DialogFooter className="gap-2 border-t border-memBorder-primary px-5 py-3 sm:flex-row sm:justify-end sm:space-x-0">
          {!job && restoringJob ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
            >
              {isZh ? "关闭" : "Close"}
            </Button>
          ) : !job ? (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={() => setOpen(false)}
              >
                {isZh ? "取消" : "Cancel"}
              </Button>
              <Button
                type="button"
                variant="primary"
                disabled={!files.length || !entitiesValid || submitting}
                onClick={() => void startImport()}
              >
                {submitting && (
                  <LoaderCircle className="mr-2 size-4 animate-spin" />
                )}
                {isZh ? "开始导入" : "Start import"}
              </Button>
            </>
          ) : isPollableJob(job) ? (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={() => setOpen(false)}
              >
                {isZh ? "后台运行" : "Run in background"}
              </Button>
              <Button
                type="button"
                variant="destructive"
                disabled={job.status === "cancelling"}
                onClick={() => void cancelImport()}
              >
                {isZh ? "取消导入" : "Cancel import"}
              </Button>
            </>
          ) : (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={() => setOpen(false)}
              >
                {isZh ? "关闭" : "Close"}
              </Button>
              <Button type="button" variant="primary" onClick={reset}>
                {isZh ? "新建导入" : "New import"}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
