// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Braces,
  CalendarDays,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Clipboard,
  Clock3,
  Copy,
  GripHorizontal,
  ListFilter,
  MessageSquareText,
  Plus,
  RefreshCw,
  Save,
  Table2,
  Tag,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import {
  addDays,
  format,
  formatDistanceToNowStrict,
  startOfDay,
  type Locale,
} from "date-fns";
import { enUS, zhCN } from "date-fns/locale";
import type { DateRange } from "react-day-picker";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import DeleteConfirmationModal from "@/components/ui/delete-confirmation-modal";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { EmptyState } from "@/components/self-hosted/empty-state";
import { TableSkeleton } from "@/components/shared/table-skeleton";
import { toast } from "@/components/ui/use-toast";
import { cn } from "@/lib/utils";
import { getErrorMessage } from "@/lib/error-message";
import { useI18n, type Language } from "@/lib/i18n";
import { buildAddMemoryCurl } from "@/lib/yiqiao-api-examples";
import { Memory } from "@/types/api";
import { api, DEFAULT_PROJECT_ID, getActiveProjectId } from "@/utils/api";
import { MEMORY_ENDPOINTS } from "@/utils/api-endpoints";
import { MemoryImportDialog } from "./memory-import-dialog";

type MatchMode = "all" | "any";
type FilterField = "entity" | "memory_id" | "category" | "metadata";
type EntityType = "user" | "agent" | "app" | "run";
type DatePreset = "all" | "1" | "7" | "30" | "custom";
type DetailTab = "details" | "source";
type FeedbackRating = "positive" | "negative" | null;

interface FilterRow {
  id: number;
  field: FilterField;
  value: string;
  entityType: EntityType;
  metadataKey: string;
}

interface CategoryFacet {
  name: string;
  count: number;
}

interface MemoryQueryResponse {
  results: Memory[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  facets: {
    total: number;
    categories: CategoryFacet[];
  };
}

interface SourceMessage {
  role?: string;
  content?: string;
  name?: string | null;
  created_at?: string;
}

interface HistoryEntry {
  id?: string;
  memory_id?: string;
  old_memory?: string | null;
  new_memory?: string | null;
  event?: string;
  created_at?: string;
  updated_at?: string;
  actor_id?: string | null;
  role?: string | null;
}

interface SavedFeedback {
  rating?: FeedbackRating;
  feedback?: string;
  reason?: string;
  updated_at?: string;
}

interface MemoryDetailsResponse {
  memory: Memory;
  source: SourceMessage[];
  history: HistoryEntry[];
  feedback: SavedFeedback | null;
}

const PAGE_SIZES = [20, 50, 100];
const QUICK_REASONS = [
  "No strong memory match",
  "Couldn't save this memory",
  "Conflicting memories detected",
];
const CATEGORY_DOTS = [
  "bg-blue-500",
  "bg-orange-500",
  "bg-emerald-500",
  "bg-violet-500",
  "bg-rose-500",
  "bg-cyan-500",
];

let nextFilterId = 1;

function createFilter(): FilterRow {
  return {
    id: nextFilterId++,
    field: "entity",
    value: "",
    entityType: "user",
    metadataKey: "",
  };
}

function categoryDot(category: string) {
  let hash = 0;
  for (let index = 0; index < category.length; index += 1) {
    hash = category.charCodeAt(index) + ((hash << 5) - hash);
  }
  return CATEGORY_DOTS[Math.abs(hash) % CATEGORY_DOTS.length];
}

function formatRelativeTime(
  value: string | undefined,
  locale: Locale,
  language: Language,
) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  const relative = formatDistanceToNowStrict(date, { addSuffix: true, locale });
  if (language === "zh") return relative;
  return relative
    .replace(/ seconds? ago$/, "s ago")
    .replace(/ minutes? ago$/, "m ago")
    .replace(/ hours? ago$/, "h ago")
    .replace(/ days? ago$/, "d ago")
    .replace(/ months? ago$/, "mo ago")
    .replace(/ years? ago$/, "y ago");
}

function formatTimestamp(
  value: string | undefined,
  locale: Locale,
  language: Language,
) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "--"
    : format(
        date,
        language === "zh" ? "yyyy年M月d日 HH:mm:ss" : "dd MMM yyyy, HH:mm:ss",
        { locale },
      );
}

function sourceRoleLabel(role: string | undefined, language: Language) {
  if (!role) return language === "zh" ? "消息" : "Message";
  const normalized = role.toLowerCase();
  const labels: Record<string, [string, string]> = {
    user: ["User", "用户"],
    assistant: ["Assistant", "智能体"],
    system: ["System", "系统"],
    tool: ["Tool", "工具"],
  };
  const label = labels[normalized];
  return label ? label[language === "zh" ? 1 : 0] : role;
}

function historyEventLabel(event: string | undefined, language: Language) {
  const normalized = (event || "update").toLowerCase();
  const labels: Record<string, [string, string]> = {
    add: ["Created", "已创建"],
    create: ["Created", "已创建"],
    update: ["Updated", "已更新"],
    delete: ["Deleted", "已删除"],
  };
  const label = labels[normalized];
  return label ? label[language === "zh" ? 1 : 0] : event || "Updated";
}

function parseMetadataValue(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) return trimmed;
  try {
    return JSON.parse(trimmed);
  } catch {
    return trimmed;
  }
}

function CategoryPill({ category }: { category: string }) {
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 rounded border border-memBorder-primary bg-surface-default-fg-secondary px-2 py-1 font-mono text-[11px] leading-none text-onSurface-default-secondary">
      <span
        className={cn("size-1.5 shrink-0 rounded-full", categoryDot(category))}
      />
      <span className="truncate">{category}</span>
    </span>
  );
}

function EntityPills({ memory }: { memory: Memory }) {
  const entities = [
    ["User", memory.user_id],
    ["Agent", memory.agent_id],
    ["App", memory.app_id],
    ["Run", memory.run_id],
  ].filter((item): item is [string, string] => Boolean(item[1]));

  if (!entities.length) {
    return <span className="text-onSurface-default-tertiary">--</span>;
  }

  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <span className="inline-flex min-w-0 items-center gap-1 rounded border border-memBorder-primary bg-surface-default-fg-secondary px-2 py-1 font-mono text-[11px] leading-none">
        <UserRound className="size-3 shrink-0" />
        <span className="truncate">{entities[0][1]}</span>
      </span>
      {entities.length > 1 && (
        <span className="rounded bg-surface-default-fg-secondary px-1.5 py-1 text-[10px] leading-none text-onSurface-default-tertiary">
          +{entities.length - 1}
        </span>
      )}
    </div>
  );
}

export default function MemoriesPage() {
  const { language } = useI18n();
  const dateLocale = language === "zh" ? zhCN : enUS;
  const [queryResult, setQueryResult] = useState<MemoryQueryResponse>({
    results: [],
    total: 0,
    page: 1,
    page_size: 20,
    total_pages: 0,
    facets: { total: 0, categories: [] },
  });
  const [isLoading, setIsLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const [matchMode, setMatchMode] = useState<MatchMode>("all");
  const [appliedFilters, setAppliedFilters] = useState<FilterRow[]>([]);
  const [draftMatchMode, setDraftMatchMode] = useState<MatchMode>("all");
  const [draftFilters, setDraftFilters] = useState<FilterRow[]>([]);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [preset, setPreset] = useState<DatePreset>("all");
  const [dateRange, setDateRange] = useState<DateRange>();
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [compactCalendar, setCompactCalendar] = useState(false);
  const [selectedMemory, setSelectedMemory] = useState<Memory | null>(null);
  const [memoryToDelete, setMemoryToDelete] = useState<Memory | null>(null);
  const [detail, setDetail] = useState<MemoryDetailsResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailTab, setDetailTab] = useState<DetailTab>("details");
  const [feedbackRating, setFeedbackRating] = useState<FeedbackRating>(null);
  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackReason, setFeedbackReason] = useState("");
  const [savingFeedback, setSavingFeedback] = useState(false);
  const [sourcePercent, setSourcePercent] = useState(54);
  const sourcePaneRef = useRef<HTMLDivElement>(null);
  const resizingSource = useRef(false);
  const requestSequence = useRef(0);
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";
  const [projectId, setProjectId] = useState(DEFAULT_PROJECT_ID);

  useEffect(() => {
    setProjectId(getActiveProjectId());
  }, []);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const sync = () => setCompactCalendar(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  const requestBody = useMemo(() => {
    const filters = appliedFilters.map((filter) => ({
      field: filter.field,
      value:
        filter.field === "metadata"
          ? parseMetadataValue(filter.value)
          : filter.value.trim(),
      ...(filter.field === "entity" ? { entity_type: filter.entityType } : {}),
      ...(filter.field === "metadata"
        ? { key: filter.metadataKey.trim() }
        : {}),
    }));
    return {
      page,
      page_size: pageSize,
      match: matchMode,
      filters,
      category: activeCategory || undefined,
      start_date: dateRange?.from
        ? format(dateRange.from, "yyyy-MM-dd")
        : undefined,
      end_date: dateRange?.to ? format(dateRange.to, "yyyy-MM-dd") : undefined,
    };
  }, [activeCategory, appliedFilters, dateRange, matchMode, page, pageSize]);

  const loadMemories = useCallback(async () => {
    const sequence = ++requestSequence.current;
    setIsLoading(true);
    try {
      const response = await api.post(MEMORY_ENDPOINTS.QUERY, requestBody);
      if (sequence !== requestSequence.current) return;
      setQueryResult(response.data as MemoryQueryResponse);
    } catch (error) {
      if (sequence !== requestSequence.current) return;
      toast({
        title: "Failed to load memories",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      if (sequence === requestSequence.current) setIsLoading(false);
    }
  }, [requestBody]);

  useEffect(() => {
    void loadMemories();
  }, [loadMemories]);

  const selectedMemoryId = selectedMemory?.id;

  useEffect(() => {
    if (selectedMemoryId === undefined) {
      setDetail(null);
      return;
    }
    let active = true;
    setDetail(null);
    setDetailLoading(true);
    setDetailTab("details");
    setFeedbackRating(null);
    setFeedbackText("");
    setFeedbackReason("");
    api
      .get(MEMORY_ENDPOINTS.DETAILS(selectedMemoryId))
      .then((response) => {
        if (!active) return;
        const nextDetail = response.data as MemoryDetailsResponse;
        setDetail(nextDetail);
        setSelectedMemory(nextDetail.memory);
        setFeedbackRating(nextDetail.feedback?.rating ?? null);
        setFeedbackText(nextDetail.feedback?.feedback ?? "");
        setFeedbackReason(nextDetail.feedback?.reason ?? "");
      })
      .catch((error) => {
        if (!active) return;
        toast({
          title: "Failed to load memory details",
          description: getErrorMessage(error),
          variant: "destructive",
        });
      })
      .finally(() => {
        if (active) setDetailLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedMemoryId]);

  useEffect(() => {
    const move = (event: PointerEvent) => {
      if (!resizingSource.current || !sourcePaneRef.current) return;
      const bounds = sourcePaneRef.current.getBoundingClientRect();
      const next = ((event.clientY - bounds.top) / bounds.height) * 100;
      setSourcePercent(Math.min(78, Math.max(24, next)));
    };
    const stop = () => {
      resizingSource.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
  }, []);

  const applyPreset = (nextPreset: Exclude<DatePreset, "custom">) => {
    setPreset(nextPreset);
    setPage(1);
    if (nextPreset === "all") {
      setDateRange(undefined);
      return;
    }
    const today = startOfDay(new Date());
    const days = Number(nextPreset);
    setDateRange({ from: addDays(today, -(days - 1)), to: today });
  };

  const selectDateRange = (nextRange: DateRange | undefined) => {
    setDateRange(nextRange);
    if (nextRange?.from && nextRange.to) {
      setPreset("custom");
      setPage(1);
      setCalendarOpen(false);
    }
  };

  const openFilters = (open: boolean) => {
    setFiltersOpen(open);
    if (open) {
      setDraftFilters(appliedFilters.map((filter) => ({ ...filter })));
      setDraftMatchMode(matchMode);
    }
  };

  const updateDraftFilter = (id: number, patch: Partial<FilterRow>) => {
    setDraftFilters((filters) =>
      filters.map((filter) =>
        filter.id === id ? { ...filter, ...patch } : filter,
      ),
    );
  };

  const applyFilters = () => {
    const validFilters = draftFilters.filter((filter) => {
      if (!filter.value.trim()) return false;
      return filter.field !== "metadata" || Boolean(filter.metadataKey.trim());
    });
    if (validFilters.length !== draftFilters.length) {
      toast({
        title: "Complete each filter",
        description: "Filter values and metadata keys cannot be empty.",
        variant: "destructive",
      });
      return;
    }
    setAppliedFilters(validFilters);
    setMatchMode(draftMatchMode);
    setPage(1);
    setFiltersOpen(false);
  };

  const handleDelete = async () => {
    if (!memoryToDelete) return;
    try {
      await api.delete(MEMORY_ENDPOINTS.BY_ID(memoryToDelete.id));
      if (selectedMemory?.id === memoryToDelete.id) setSelectedMemory(null);
      setMemoryToDelete(null);
      toast({ title: "Memory deleted", variant: "success" });
      await loadMemories();
    } catch (error) {
      toast({
        title: "Failed to delete memory",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const saveFeedback = async () => {
    if (!selectedMemory) return;
    setSavingFeedback(true);
    try {
      const response = await api.post(
        MEMORY_ENDPOINTS.FEEDBACK(selectedMemory.id),
        {
          rating: feedbackRating,
          feedback: feedbackText,
          reason: feedbackReason,
        },
      );
      setDetail((current) =>
        current
          ? { ...current, feedback: response.data.feedback as SavedFeedback }
          : current,
      );
      toast({ title: "Feedback saved", variant: "success" });
    } catch (error) {
      toast({
        title: "Failed to save feedback",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setSavingFeedback(false);
    }
  };

  const copyText = async (text: string, label: string) => {
    await navigator.clipboard.writeText(text);
    toast({ title: `${label} copied`, variant: "success" });
  };

  const selectedIndex = selectedMemory
    ? queryResult.results.findIndex((memory) => memory.id === selectedMemory.id)
    : -1;
  const visibleMemory = detail?.memory ?? selectedMemory;
  const metadataEntries = Object.entries(visibleMemory?.metadata ?? {});
  const pageStart = queryResult.total ? (page - 1) * pageSize + 1 : 0;
  const pageEnd = Math.min(page * pageSize, queryResult.total);

  return (
    <div className="mx-auto w-full max-w-[1440px] space-y-4 pb-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <h1 className="text-xl font-semibold text-onSurface-default-primary">
          Memories
        </h1>
        <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
          <MemoryImportDialog language={language} onImported={loadMemories} />
          <Popover open={calendarOpen} onOpenChange={setCalendarOpen}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                className={cn(
                  "h-9 w-full justify-between px-3 text-xs font-normal sm:w-[178px]",
                  preset === "custom" && "border-onSurface-default-primary",
                )}
              >
                <span className="truncate">
                  {dateRange?.from && dateRange.to
                    ? `${format(dateRange.from, "MMM d", { locale: dateLocale })} - ${format(dateRange.to, "MMM d, yyyy", { locale: dateLocale })}`
                    : "Pick a date range"}
                </span>
                <CalendarDays className="ml-2 size-3.5 shrink-0" />
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-auto p-0">
              <Calendar
                initialFocus
                mode="range"
                defaultMonth={dateRange?.from}
                selected={dateRange}
                onSelect={selectDateRange}
                numberOfMonths={compactCalendar ? 1 : 2}
                disabled={{ after: new Date() }}
              />
            </PopoverContent>
          </Popover>
          <div className="grid h-9 grid-cols-4 rounded-md border border-memBorder-primary bg-surface-default-secondary p-0.5">
            {[
              ["all", language === "zh" ? "全部时间" : "All Time"],
              ["1", language === "zh" ? "1 天" : "1d"],
              ["7", language === "zh" ? "7 天" : "7d"],
              ["30", language === "zh" ? "30 天" : "30d"],
            ].map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={cn(
                  "h-8 min-w-14 rounded px-2 text-xs font-medium transition-colors",
                  preset === value
                    ? "bg-surface-default-primary text-onSurface-default-primary shadow-sm"
                    : "text-onSurface-default-secondary hover:bg-surface-default-tertiary-hover",
                )}
                onClick={() =>
                  applyPreset(value as Exclude<DatePreset, "custom">)
                }
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-2 overflow-x-auto pb-1 sm:pb-0">
          <button
            type="button"
            onClick={() => {
              setActiveCategory(null);
              setPage(1);
            }}
            className={cn(
              "inline-flex h-9 shrink-0 items-center gap-2 rounded-md border px-3 text-xs font-medium",
              !activeCategory
                ? "border-memBorder-primary bg-surface-default-tertiary"
                : "border-transparent hover:border-memBorder-primary",
            )}
          >
            <Table2 className="size-3.5" />
            Overview
            <span className="text-[10px] text-onSurface-default-tertiary">
              {queryResult.facets.total}
            </span>
          </button>
          {queryResult.facets.categories.map((category) => (
            <button
              key={category.name}
              type="button"
              onClick={() => {
                setActiveCategory(category.name);
                setPage(1);
              }}
              className={cn(
                "inline-flex h-9 max-w-[220px] shrink-0 items-center gap-2 rounded-md border px-3 text-xs font-medium",
                activeCategory === category.name
                  ? "border-memBorder-primary bg-surface-default-tertiary"
                  : "border-memBorder-primary bg-surface-default-primary hover:bg-surface-default-primary-hover",
              )}
            >
              <span
                className={cn(
                  "size-2 shrink-0 rounded-full",
                  categoryDot(category.name),
                )}
              />
              <span className="truncate">{category.name}</span>
              <span className="text-[10px] text-onSurface-default-tertiary">
                {category.count}
              </span>
            </button>
          ))}
        </div>

        <div className="flex shrink-0 items-center justify-end gap-2">
          <Popover open={filtersOpen} onOpenChange={openFilters}>
            <PopoverTrigger asChild>
              <Button variant="outline" size="sm" className="gap-2">
                <ListFilter className="size-3.5" />
                Filters
                {appliedFilters.length > 0 && (
                  <span className="flex size-4 items-center justify-center rounded-full bg-onSurface-default-primary text-[9px] text-surface-default-primary">
                    {appliedFilters.length}
                  </span>
                )}
              </Button>
            </PopoverTrigger>
            <PopoverContent
              align="end"
              className="w-[min(calc(100vw-32px),620px)] p-0"
            >
              <div className="flex items-start justify-between gap-3 border-b border-memBorder-primary px-3 py-3 sm:px-4">
                <div className="min-w-0">
                  <p className="text-sm font-semibold">Filter memories</p>
                  <p className="text-xs text-onSurface-default-tertiary">
                    Match {draftMatchMode} conditions
                  </p>
                </div>
                <div className="grid shrink-0 grid-cols-2 rounded-md border border-memBorder-primary bg-surface-default-secondary p-0.5">
                  {(["all", "any"] as MatchMode[]).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      onClick={() => setDraftMatchMode(mode)}
                      className={cn(
                        "h-7 rounded px-3 text-xs capitalize",
                        draftMatchMode === mode &&
                          "bg-surface-default-primary shadow-sm",
                      )}
                    >
                      {mode}
                    </button>
                  ))}
                </div>
              </div>
              <div className="max-h-[360px] space-y-2 overflow-y-auto p-3 sm:p-4">
                {draftFilters.length === 0 ? (
                  <div className="rounded-md border border-dashed border-memBorder-primary px-4 py-8 text-center text-xs text-onSurface-default-tertiary">
                    No filter conditions
                  </div>
                ) : (
                  draftFilters.map((filter) => (
                    <div
                      key={filter.id}
                      className="flex flex-wrap items-center gap-2 rounded-md border border-memBorder-primary bg-surface-default-secondary p-2"
                    >
                      <Select
                        value={filter.field}
                        onValueChange={(value: FilterField) =>
                          updateDraftFilter(filter.id, {
                            field: value,
                            value: "",
                            metadataKey: "",
                          })
                        }
                      >
                        <SelectTrigger className="h-8 w-[124px] bg-surface-default-primary text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="entity">Entity</SelectItem>
                          <SelectItem value="memory_id">Memory ID</SelectItem>
                          <SelectItem value="category">Category</SelectItem>
                          <SelectItem value="metadata">Metadata</SelectItem>
                        </SelectContent>
                      </Select>
                      {filter.field === "entity" && (
                        <Select
                          value={filter.entityType}
                          onValueChange={(value: EntityType) =>
                            updateDraftFilter(filter.id, { entityType: value })
                          }
                        >
                          <SelectTrigger className="h-8 w-[92px] bg-surface-default-primary text-xs capitalize">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="user">User</SelectItem>
                            <SelectItem value="agent">Agent</SelectItem>
                            <SelectItem value="app">App</SelectItem>
                            <SelectItem value="run">Run</SelectItem>
                          </SelectContent>
                        </Select>
                      )}
                      {filter.field === "metadata" && (
                        <Input
                          value={filter.metadataKey}
                          onChange={(event) =>
                            updateDraftFilter(filter.id, {
                              metadataKey: event.target.value,
                            })
                          }
                          placeholder="key or nested.key"
                          className="h-8 min-w-[138px] flex-1 bg-surface-default-primary text-xs"
                        />
                      )}
                      <Input
                        value={filter.value}
                        onChange={(event) =>
                          updateDraftFilter(filter.id, {
                            value: event.target.value,
                          })
                        }
                        placeholder={
                          filter.field === "entity"
                            ? `${filter.entityType} ID`
                            : filter.field === "metadata"
                              ? "value"
                              : filter.field === "memory_id"
                                ? "Memory ID"
                                : "Category name"
                        }
                        className="h-8 min-w-[160px] flex-1 bg-surface-default-primary text-xs"
                      />
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-8 shrink-0"
                        aria-label="Remove filter"
                        title="Remove filter"
                        onClick={() =>
                          setDraftFilters((filters) =>
                            filters.filter((item) => item.id !== filter.id),
                          )
                        }
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  ))
                )}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    setDraftFilters((filters) => [...filters, createFilter()])
                  }
                >
                  <Plus className="mr-1.5 size-3.5" />
                  Add filter
                </Button>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-memBorder-primary px-3 py-3 sm:px-4">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!draftFilters.length}
                  onClick={() => setDraftFilters([])}
                >
                  Clear all
                </Button>
                <div className="flex flex-wrap justify-end gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setFiltersOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button variant="primary" size="sm" onClick={applyFilters}>
                    Apply filters
                  </Button>
                </div>
              </div>
            </PopoverContent>
          </Popover>
          <Button
            variant="outline"
            size="sm"
            aria-label="Refresh memories"
            title="Refresh memories"
            onClick={() => void loadMemories()}
            disabled={isLoading}
          >
            <RefreshCw
              className={cn("size-3.5", isLoading && "animate-spin")}
            />
            <span className="ml-2 hidden sm:inline">Refresh</span>
          </Button>
        </div>
      </div>

      {isLoading && !queryResult.results.length ? (
        <TableSkeleton rows={10} columns={5} />
      ) : !queryResult.results.length ? (
        <div className="rounded-md border border-memBorder-primary">
          {appliedFilters.length || activeCategory || dateRange ? (
            <EmptyState
              title="No matching memories"
              description="Adjust the active date range or filters."
            />
          ) : (
            <EmptyState
              title="No memories yet"
              description="Create your first memory by sending a POST /memories request."
            >
              <pre className="mt-3 max-w-lg overflow-x-auto rounded bg-surface-default-secondary p-3 text-left font-mono text-xs">
                {buildAddMemoryCurl({
                  apiUrl,
                  apiKey: "<your-api-key>",
                  projectId,
                })}
              </pre>
            </EmptyState>
          )}
        </div>
      ) : (
        <div
          role="table"
          aria-label="Memories"
          className={cn(
            "overflow-hidden rounded-md border border-memBorder-primary bg-surface-default-primary",
            isLoading && "opacity-60",
          )}
        >
          <div
            role="row"
            className="grid h-[38px] grid-cols-[86px_minmax(0,1fr)_42px] items-center border-b border-memBorder-primary bg-surface-default-fg-secondary px-2 text-xs text-onSurface-default-secondary lg:grid-cols-[155px_minmax(150px,1fr)_minmax(240px,1.55fr)_minmax(155px,.9fr)_48px] lg:px-0"
          >
            <div role="columnheader" className="flex items-center gap-2 px-2">
              <Clock3 className="size-3.5" />
              Time
            </div>
            <div
              role="columnheader"
              className="hidden items-center gap-2 border-l border-memBorder-primary px-4 lg:flex"
            >
              <UserRound className="size-3.5" />
              Entities
            </div>
            <div
              role="columnheader"
              className="flex items-center gap-2 px-2 lg:border-l lg:border-memBorder-primary lg:px-4"
            >
              <MessageSquareText className="size-3.5" />
              Memory Content
            </div>
            <div
              role="columnheader"
              className="hidden items-center gap-2 border-l border-memBorder-primary px-4 lg:flex"
            >
              <Tag className="size-3.5" />
              Categories
            </div>
            <div role="columnheader" className="text-center">
              <span className="sr-only">Action</span>
              ...
            </div>
          </div>
          {queryResult.results.map((memory) => (
            <div
              key={memory.id}
              role="row"
              tabIndex={0}
              onClick={() => setSelectedMemory(memory)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setSelectedMemory(memory);
                }
              }}
              className={cn(
                "grid min-h-[41px] cursor-pointer grid-cols-[86px_minmax(0,1fr)_42px] items-center border-b border-memBorder-primary px-2 text-xs last:border-b-0 hover:bg-surface-default-primary-hover lg:grid-cols-[155px_minmax(150px,1fr)_minmax(240px,1.55fr)_minmax(155px,.9fr)_48px] lg:px-0",
                selectedMemory?.id === memory.id &&
                  "bg-surface-default-tertiary",
              )}
            >
              <div
                role="cell"
                className="truncate px-2 text-onSurface-default-secondary"
              >
                {formatRelativeTime(memory.created_at, dateLocale, language)}
              </div>
              <div role="cell" className="hidden min-w-0 px-4 lg:block">
                <EntityPills memory={memory} />
              </div>
              <div role="cell" className="min-w-0 px-2 lg:px-4">
                <p className="truncate text-sm text-onSurface-default-primary">
                  {memory.memory || "--"}
                </p>
              </div>
              <div
                role="cell"
                className="hidden min-w-0 items-center gap-1.5 px-4 lg:flex"
              >
                {memory.categories?.[0] ? (
                  <>
                    <CategoryPill category={memory.categories[0]} />
                    {memory.categories.length > 1 && (
                      <span className="shrink-0 rounded bg-surface-default-fg-secondary px-1.5 py-1 font-mono text-[10px]">
                        +{memory.categories.length - 1}
                      </span>
                    )}
                  </>
                ) : (
                  <span className="text-onSurface-default-tertiary">--</span>
                )}
              </div>
              <div role="cell" className="flex justify-center">
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8 text-onSurface-default-secondary hover:text-onSurface-danger-primary"
                  aria-label="Delete memory"
                  title="Delete memory"
                  onClick={(event) => {
                    event.stopPropagation();
                    setMemoryToDelete(memory);
                  }}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {queryResult.total > 0 && (
        <div className="flex flex-col gap-3 text-xs text-onSurface-default-tertiary sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2">
            <span>
              {pageStart}-{pageEnd} of {queryResult.total}
            </span>
            <Select
              value={String(pageSize)}
              onValueChange={(value) => {
                setPageSize(Number(value));
                setPage(1);
              }}
            >
              <SelectTrigger className="h-8 w-[92px] text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PAGE_SIZES.map((size) => (
                  <SelectItem key={size} value={String(size)}>
                    {size} / page
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((current) => current - 1)}
            >
              <ChevronLeft className="mr-1 size-3.5" />
              Previous
            </Button>
            <span className="min-w-16 text-center">
              {page} / {Math.max(queryResult.total_pages, 1)}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= queryResult.total_pages}
              onClick={() => setPage((current) => current + 1)}
            >
              Next
              <ChevronRight className="ml-1 size-3.5" />
            </Button>
          </div>
        </div>
      )}

      <Sheet
        open={Boolean(selectedMemory)}
        onOpenChange={(open) => {
          if (!open) setSelectedMemory(null);
        }}
      >
        <SheetContent className="w-full gap-0 overflow-hidden p-0 [&>button]:hidden sm:w-[720px] sm:max-w-[min(720px,calc(100vw-32px))]">
          <SheetHeader className="flex h-12 flex-row items-center justify-between space-y-0 border-b border-memBorder-primary px-3 text-left">
            <SheetTitle className="text-lg">Memory Details</SheetTitle>
            <SheetDescription className="sr-only">
              Inspect memory details, source messages, updates, and feedback.
            </SheetDescription>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                disabled={selectedIndex <= 0}
                aria-label="Previous memory"
                title="Previous memory"
                onClick={() =>
                  setSelectedMemory(queryResult.results[selectedIndex - 1])
                }
              >
                <ChevronUp className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                disabled={
                  selectedIndex < 0 ||
                  selectedIndex >= queryResult.results.length - 1
                }
                aria-label="Next memory"
                title="Next memory"
                onClick={() =>
                  setSelectedMemory(queryResult.results[selectedIndex + 1])
                }
              >
                <ChevronDown className="size-4" />
              </Button>
              <SheetClose asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8"
                  aria-label="Close details"
                  title="Close details"
                >
                  <X className="size-4" />
                </Button>
              </SheetClose>
            </div>
          </SheetHeader>

          <div className="grid h-9 grid-cols-2 border-b border-memBorder-primary">
            {(
              [
                ["details", "Details"],
                ["source", "Source & Updates"],
              ] as [DetailTab, string][]
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setDetailTab(value)}
                className={cn(
                  "relative text-xs text-onSurface-default-secondary",
                  detailTab === value &&
                    "text-onSurface-default-primary after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:bg-onSurface-default-primary",
                )}
              >
                {label}
              </button>
            ))}
          </div>

          {detailLoading || !visibleMemory ? (
            <div className="p-4">
              <TableSkeleton rows={8} columns={1} />
            </div>
          ) : detailTab === "details" ? (
            <div className="h-[calc(100vh-84px)] overflow-y-auto p-2">
              <div className="overflow-hidden rounded-md border border-memBorder-primary">
                <div className="flex min-h-9 items-center justify-between gap-2 bg-surface-default-fg-secondary px-2 text-xs">
                  <span className="min-w-0 truncate font-mono">
                    <span className="mr-2 font-sans text-onSurface-default-tertiary">
                      ID
                    </span>
                    {visibleMemory.id}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7 shrink-0"
                    aria-label="Copy memory ID"
                    title="Copy memory ID"
                    onClick={() => void copyText(visibleMemory.id, "Memory ID")}
                  >
                    <Copy className="size-3.5" />
                  </Button>
                </div>
                <div className="border-t border-memBorder-primary p-2">
                  <p className="whitespace-pre-wrap text-sm leading-6">
                    {visibleMemory.memory}
                  </p>
                  {visibleMemory.categories?.length ? (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {visibleMemory.categories.map((category) => (
                        <CategoryPill key={category} category={category} />
                      ))}
                    </div>
                  ) : null}
                </div>
                <div className="grid grid-cols-2 border-t border-memBorder-primary text-xs">
                  <div className="border-r border-memBorder-primary p-2">
                    <p className="text-onSurface-default-tertiary">
                      Created On
                    </p>
                    <p className="mt-1">
                      {formatTimestamp(
                        visibleMemory.created_at,
                        dateLocale,
                        language,
                      )}
                    </p>
                  </div>
                  <div className="p-2">
                    <p className="text-onSurface-default-tertiary">
                      Updated On
                    </p>
                    <p className="mt-1">
                      {formatTimestamp(
                        visibleMemory.updated_at ?? visibleMemory.created_at,
                        dateLocale,
                        language,
                      )}
                    </p>
                  </div>
                </div>
                <div className="space-y-0 border-t border-memBorder-primary">
                  {[
                    ["User", visibleMemory.user_id],
                    ["Agent", visibleMemory.agent_id],
                    ["App", visibleMemory.app_id],
                    ["Run", visibleMemory.run_id],
                  ]
                    .filter((item): item is [string, string] =>
                      Boolean(item[1]),
                    )
                    .map(([label, value]) => (
                      <div
                        key={label}
                        className="flex min-h-9 items-center justify-between gap-3 border-b border-memBorder-primary px-2 text-xs last:border-b-0"
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          <UserRound className="size-3.5 text-blue-600" />
                          <span className="text-onSurface-default-tertiary">
                            {label}:
                          </span>
                          <span className="truncate font-mono">{value}</span>
                        </span>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-7 shrink-0"
                          aria-label={`Copy ${label} ID`}
                          title={`Copy ${label} ID`}
                          onClick={() => void copyText(value, `${label} ID`)}
                        >
                          <Copy className="size-3.5" />
                        </Button>
                      </div>
                    ))}
                </div>
                <div className="border-t border-memBorder-primary">
                  <div className="flex h-9 items-center gap-2 px-2 text-xs font-medium">
                    <Braces className="size-3.5 text-blue-600" />
                    Metadata
                  </div>
                  {metadataEntries.length ? (
                    <pre className="max-h-60 overflow-auto border-t border-memBorder-primary bg-surface-default-secondary p-3 font-mono text-[11px] leading-5">
                      {JSON.stringify(visibleMemory.metadata, null, 2)}
                    </pre>
                  ) : (
                    <div className="flex min-h-40 flex-col items-center justify-center border-t border-memBorder-primary text-onSurface-default-tertiary">
                      <Clipboard className="mb-3 size-8 opacity-30" />
                      <p className="text-xs">No metadata available</p>
                    </div>
                  )}
                </div>
              </div>

              <div className="mt-2 overflow-hidden rounded-md border border-memBorder-primary">
                <div className="flex h-9 items-center justify-between border-b border-memBorder-primary bg-surface-default-fg-secondary px-2">
                  <span className="flex items-center gap-2 text-xs font-medium">
                    <MessageSquareText className="size-3.5 text-blue-600" />
                    Feedback
                  </span>
                  <Button
                    variant="primary"
                    size="xs"
                    disabled={
                      savingFeedback ||
                      (!feedbackRating &&
                        !feedbackText.trim() &&
                        !feedbackReason)
                    }
                    onClick={() => void saveFeedback()}
                  >
                    <Save className="mr-1 size-3" />
                    {savingFeedback ? "Saving" : "Save"}
                  </Button>
                </div>
                <div className="space-y-2 p-2">
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      className={cn(
                        "size-8",
                        feedbackRating === "positive" &&
                          "bg-emerald-50 text-emerald-700",
                      )}
                      aria-label="Positive feedback"
                      title="Positive feedback"
                      onClick={() =>
                        setFeedbackRating((rating) =>
                          rating === "positive" ? null : "positive",
                        )
                      }
                    >
                      <ThumbsUp className="size-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className={cn(
                        "size-8",
                        feedbackRating === "negative" &&
                          "bg-rose-50 text-rose-700",
                      )}
                      aria-label="Negative feedback"
                      title="Negative feedback"
                      onClick={() =>
                        setFeedbackRating((rating) =>
                          rating === "negative" ? null : "negative",
                        )
                      }
                    >
                      <ThumbsDown className="size-4" />
                    </Button>
                  </div>
                  <Textarea
                    value={feedbackText}
                    onChange={(event) => setFeedbackText(event.target.value)}
                    placeholder="Write your feedback"
                    maxLength={4000}
                    className="min-h-28 resize-y text-sm"
                  />
                  <div className="flex flex-wrap gap-1.5">
                    {QUICK_REASONS.map((reason) => (
                      <button
                        key={reason}
                        type="button"
                        onClick={() =>
                          setFeedbackReason((current) =>
                            current === reason ? "" : reason,
                          )
                        }
                        className={cn(
                          "rounded-md border border-memBorder-primary px-3 py-1.5 text-xs hover:bg-surface-default-primary-hover",
                          feedbackReason === reason &&
                            "border-onSurface-default-primary bg-surface-default-tertiary",
                        )}
                      >
                        {reason}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <div
              ref={sourcePaneRef}
              className="grid h-[calc(100vh-84px)] min-h-0"
              style={{
                gridTemplateRows: `${sourcePercent}% 13px minmax(0, 1fr)`,
              }}
            >
              <section className="min-h-0 overflow-y-auto">
                <div className="sticky top-0 z-10 flex h-8 items-center justify-between border-b border-memBorder-primary bg-surface-default-primary px-2 text-xs font-medium">
                  <span>Source</span>
                  <span className="text-[10px] font-normal text-onSurface-default-tertiary">
                    Scroll to see more
                  </span>
                </div>
                {detail?.source.length ? (
                  <div className="space-y-2 p-3">
                    {detail.source.map((message, index) => (
                      <div
                        key={`${message.created_at ?? "source"}-${index}`}
                        className="rounded-md border border-memBorder-primary bg-surface-default-secondary p-3"
                      >
                        <div className="mb-2 flex items-center justify-between gap-3 text-[11px]">
                          <span className="inline-flex items-center gap-1.5 font-medium capitalize">
                            <UserRound className="size-3.5" />
                            {message.name ||
                              sourceRoleLabel(message.role, language)}
                          </span>
                          <span className="text-onSurface-default-tertiary">
                            {formatTimestamp(
                              message.created_at,
                              dateLocale,
                              language,
                            )}
                          </span>
                        </div>
                        <p className="whitespace-pre-wrap text-sm leading-5">
                          {message.content || "--"}
                        </p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="flex h-[calc(100%-32px)] min-h-32 items-center justify-center text-xs text-onSurface-default-tertiary">
                    No messages to show
                  </div>
                )}
              </section>

              <button
                type="button"
                className="relative flex cursor-row-resize items-center justify-center border-y border-memBorder-primary bg-surface-default-secondary"
                aria-label="Resize source and changelog panes"
                title="Drag to resize"
                onPointerDown={(event) => {
                  event.preventDefault();
                  resizingSource.current = true;
                  document.body.style.cursor = "row-resize";
                  document.body.style.userSelect = "none";
                }}
              >
                <span className="absolute flex h-6 w-8 items-center justify-center rounded-md border border-memBorder-primary bg-surface-default-primary">
                  <GripHorizontal className="size-3.5" />
                </span>
              </button>

              <section className="min-h-0 overflow-y-auto">
                <div className="sticky top-0 z-10 flex h-8 items-center border-b border-memBorder-primary bg-surface-default-primary px-2 text-xs font-medium">
                  Changelog
                </div>
                {detail?.history.length ? (
                  <div className="space-y-3 p-3">
                    {[...detail.history].reverse().map((entry, index) => (
                      <div
                        key={entry.id ?? `${entry.updated_at}-${index}`}
                        className="grid grid-cols-[28px_minmax(0,1fr)] gap-2"
                      >
                        <div className="flex size-6 items-center justify-center rounded-full bg-blue-700 text-[10px] text-white">
                          {detail.history.length - index}
                        </div>
                        <div className="rounded-md border border-memBorder-primary bg-surface-default-secondary p-2">
                          <div className="flex items-start justify-between gap-2">
                            <span className="font-mono text-[11px]">
                              {entry.memory_id ?? visibleMemory.id}
                            </span>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="size-7 shrink-0"
                              aria-label="Copy changelog entry"
                              title="Copy changelog entry"
                              onClick={() =>
                                void copyText(
                                  entry.new_memory || entry.old_memory || "",
                                  "Changelog entry",
                                )
                              }
                            >
                              <Copy className="size-3.5" />
                            </Button>
                          </div>
                          <p className="mt-1 whitespace-pre-wrap text-sm">
                            {entry.new_memory ||
                              entry.old_memory ||
                              "Memory deleted"}
                          </p>
                          <p className="mt-2 text-[11px] text-onSurface-default-tertiary">
                            {historyEventLabel(entry.event, language)}
                            {language === "zh" ? "于" : " at "}
                            {formatTimestamp(
                              entry.updated_at || entry.created_at,
                              dateLocale,
                              language,
                            )}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="flex h-[calc(100%-32px)] min-h-32 items-center justify-center text-xs text-onSurface-default-tertiary">
                    No updates to show
                  </div>
                )}
              </section>
            </div>
          )}
        </SheetContent>
      </Sheet>

      <DeleteConfirmationModal
        isOpen={Boolean(memoryToDelete)}
        onClose={() => setMemoryToDelete(null)}
        onConfirm={handleDelete}
        title="Delete memory"
        description="This memory will be permanently removed. This cannot be undone."
        itemName={memoryToDelete?.id ?? ""}
        confirmButtonText="Delete"
      />
    </div>
  );
}
