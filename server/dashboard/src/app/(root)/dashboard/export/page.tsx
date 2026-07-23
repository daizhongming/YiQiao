// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { DateRange } from "react-day-picker";
import {
  CalendarDays,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Code2,
  Download,
  FileOutput,
  Hash,
  Hourglass,
  Layers3,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  UserRound,
  UsersRound,
  X,
} from "lucide-react";
import { format, subMonths, type Locale } from "date-fns";
import { enUS, zhCN } from "date-fns/locale";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { Textarea } from "@/components/ui/textarea";
import { DataTable } from "@/components/shared/data-table";
import { TableSkeleton } from "@/components/shared/table-skeleton";
import { toast } from "@/components/ui/use-toast";
import { getErrorMessage } from "@/lib/error-message";
import { useI18n, type Language } from "@/lib/i18n";
import useDebounce from "@/hooks/useDebounce";
import { api } from "@/utils/api";
import { EXPORT_ENDPOINTS } from "@/utils/api-endpoints";
import { MemoryExportJob, MemoryExportPage } from "@/types/api";

const PAGE_SIZE = 10;
const DEFAULT_RAW_FILTERS =
  '{\n  "AND": [\n    { "user_id": "user_123" }\n  ]\n}';
const ENTITY_TYPES = [
  { value: "user_id", label: "User" },
  { value: "agent_id", label: "Agent" },
  { value: "run_id", label: "Run" },
  { value: "app_id", label: "App" },
] as const;

type EntityType = (typeof ENTITY_TYPES)[number]["value"];
type BuilderMode = "visual" | "raw";
type EntityFilter = { id: number; type: EntityType; value: string };

const EMPTY_PAGE: MemoryExportPage = {
  items: [],
  total: 0,
  page: 1,
  page_size: PAGE_SIZE,
  total_pages: 0,
  has_next: false,
  has_previous: false,
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);

const parseObject = (source: string, label: string) => {
  const parsed: unknown = JSON.parse(source || "{}");
  if (!isRecord(parsed)) throw new Error(`${label} must be a JSON object.`);
  return parsed;
};

const entityLabel = (key: string, language: Language) => {
  const entity = ENTITY_TYPES.find((item) => item.value === key);
  if (!entity) return undefined;
  if (language === "en") return entity.label;
  return {
    user_id: "用户",
    agent_id: "智能体",
    run_id: "运行",
    app_id: "应用",
  }[entity.value];
};

const getEntitySummary = (
  entity: Record<string, unknown>,
  language: Language,
) => {
  const labels: string[] = [];
  const visit = (value: unknown) => {
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (!isRecord(value)) return;
    Object.entries(value).forEach(([key, nested]) => {
      const label = entityLabel(key, language);
      if (label && nested != null && String(nested).trim()) {
        labels.push(`${label}: ${String(nested)}`);
      } else if (
        key.replace(/^\$/, "").toUpperCase() === "AND" ||
        key.replace(/^\$/, "").toUpperCase() === "OR"
      ) {
        visit(nested);
      }
    });
  };
  visit(entity);
  return labels.length
    ? [...new Set(labels)].join(", ")
    : language === "zh"
      ? "所有记忆"
      : "All memories";
};

const formatTimestamp = (
  value: string | null,
  language: Language,
  locale: Locale,
) => {
  if (!value) return "--";
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime())
    ? "--"
    : format(
        timestamp,
        language === "zh" ? "yyyy年M月d日 HH:mm" : "MMM d, yyyy, h:mm a",
        { locale },
      );
};

export default function ExportPage() {
  const { language } = useI18n();
  const dateLocale = language === "zh" ? zhCN : enUS;
  const [jobsPage, setJobsPage] = useState<MemoryExportPage>(EMPTY_PAGE);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, 300);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [mode, setMode] = useState<BuilderMode>("visual");
  const [filters, setFilters] = useState<EntityFilter[]>([
    { id: 1, type: "user_id", value: "" },
  ]);
  const [nextFilterId, setNextFilterId] = useState(2);
  const [rawFilters, setRawFilters] = useState(DEFAULT_RAW_FILTERS);
  const [dateRange, setDateRange] = useState<DateRange>();
  const [schema, setSchema] = useState("{}");
  const [creating, setCreating] = useState(false);
  const [downloadingId, setDownloadingId] = useState("");

  const loadJobs = useCallback(
    async (targetPage: number, targetSearch: string, refresh = false) => {
      refresh ? setIsRefreshing(true) : setIsLoading(true);
      try {
        const response = await api.get<MemoryExportPage>(
          EXPORT_ENDPOINTS.BASE,
          {
            params: {
              page: targetPage,
              page_size: PAGE_SIZE,
              search: targetSearch.trim(),
            },
          },
        );
        setJobsPage(response.data);
      } catch (error) {
        toast({
          title: "Failed to load memory exports",
          description: getErrorMessage(error),
          variant: "destructive",
        });
      } finally {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    },
    [],
  );

  useEffect(() => {
    void loadJobs(page, debouncedSearch);
  }, [debouncedSearch, loadJobs, page]);

  const schemaIsValid = useMemo(() => {
    try {
      parseObject(schema, "Pydantic schema");
      return true;
    } catch {
      return false;
    }
  }, [schema]);

  const rawFiltersAreValid = useMemo(() => {
    try {
      parseObject(rawFilters, "Filters");
      return true;
    } catch {
      return false;
    }
  }, [rawFilters]);

  const visualFiltersAreValid = filters.every(
    (filter) => filter.value.trim().length > 0,
  );
  const canCreate =
    schemaIsValid &&
    (mode === "raw" ? rawFiltersAreValid : visualFiltersAreValid);

  const resetBuilder = () => {
    setMode("visual");
    setFilters([{ id: 1, type: "user_id", value: "" }]);
    setNextFilterId(2);
    setRawFilters(DEFAULT_RAW_FILTERS);
    setDateRange(undefined);
    setSchema("{}");
  };

  const updateFilter = (
    id: number,
    patch: Partial<Pick<EntityFilter, "type" | "value">>,
  ) => {
    setFilters((current) =>
      current.map((filter) =>
        filter.id === id ? { ...filter, ...patch } : filter,
      ),
    );
  };

  const addFilter = () => {
    setFilters((current) => [
      ...current,
      { id: nextFilterId, type: "user_id", value: "" },
    ]);
    setNextFilterId((current) => current + 1);
  };

  const createExport = async () => {
    try {
      const parsedFilters =
        mode === "raw"
          ? parseObject(rawFilters, "Filters")
          : {
              AND: filters.map((filter) => ({
                [filter.type]: filter.value.trim(),
              })),
            };
      const parsedSchema = parseObject(schema, "Pydantic schema");
      const payload: Record<string, unknown> = {
        filters: parsedFilters,
        pydantic_schema: parsedSchema,
      };
      if (dateRange?.from || dateRange?.to) {
        payload.date_range = {
          ...(dateRange.from && {
            start: format(dateRange.from, "yyyy-MM-dd"),
          }),
          ...(dateRange.to && { end: format(dateRange.to, "yyyy-MM-dd") }),
        };
      }

      setCreating(true);
      await api.post<MemoryExportJob>(EXPORT_ENDPOINTS.BASE, payload);
      toast({ title: "Memory export created", variant: "success" });
      setIsCreateOpen(false);
      resetBuilder();
      setSearch("");
      setPage(1);
      await loadJobs(1, "", true);
    } catch (error) {
      toast({
        title: "Failed to create export",
        description: getErrorMessage(
          error,
          "Check that filters and schema are valid JSON.",
        ),
        variant: "destructive",
      });
    } finally {
      setCreating(false);
    }
  };

  const downloadExport = async (job: MemoryExportJob) => {
    setDownloadingId(job.id);
    try {
      const response = await api.get<MemoryExportJob>(
        EXPORT_ENDPOINTS.BY_ID(job.id),
      );
      const blob = new Blob(
        [JSON.stringify(response.data.result ?? {}, null, 2)],
        { type: "application/json" },
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `yiqiao-memory-export-${job.id}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast({
        title: "Failed to download export",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setDownloadingId("");
    }
  };

  const columns = [
    {
      key: "id" as keyof MemoryExportJob,
      label: "ID",
      icon: Hash,
      width: 190,
      render: (value: string) => (
        <span className="block truncate font-mono text-xs" title={value}>
          {value}
        </span>
      ),
    },
    {
      key: "status" as keyof MemoryExportJob,
      label: "Status",
      icon: Hourglass,
      width: 130,
      render: (value: string) => (
        <span className="inline-flex items-center gap-1.5 capitalize">
          <CheckCircle2 className="size-3.5 text-onSurface-positive-primary" />
          {language === "zh"
            ? ({
                completed: "已完成",
                pending: "等待中",
                processing: "处理中",
                failed: "失败",
              }[value.toLowerCase()] ?? value)
            : value}
        </span>
      ),
    },
    {
      key: "entity" as keyof MemoryExportJob,
      label: "Entity",
      icon: UsersRound,
      width: 240,
      render: (value: Record<string, unknown>) => (
        <span
          className="block truncate"
          title={getEntitySummary(value, language)}
        >
          {getEntitySummary(value, language)}
        </span>
      ),
    },
    {
      key: "started_at" as keyof MemoryExportJob,
      label: "Started",
      icon: CalendarDays,
      width: 185,
      render: (value: string) => formatTimestamp(value, language, dateLocale),
    },
    {
      key: "completed_at" as keyof MemoryExportJob,
      label: "Completed",
      icon: Clock3,
      width: 185,
      render: (value: string | null) =>
        formatTimestamp(value, language, dateLocale),
    },
    {
      key: "filters" as keyof MemoryExportJob,
      label: "Actions",
      width: 80,
      align: "right" as const,
      render: (_value: Record<string, unknown>, row: MemoryExportJob) => (
        <div className="flex justify-end">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-8"
            onClick={() => void downloadExport(row)}
            disabled={row.status !== "completed" || downloadingId === row.id}
            aria-label="Download export"
            title="Download export"
          >
            <Download
              className={`size-4 ${downloadingId === row.id ? "animate-pulse" : ""}`}
            />
          </Button>
        </div>
      ),
    },
  ];
  const mobileColumns = [columns[0], columns[1], columns[5]];

  const firstVisible = jobsPage.total
    ? (jobsPage.page - 1) * jobsPage.page_size + 1
    : 0;
  const lastVisible = Math.min(
    jobsPage.page * jobsPage.page_size,
    jobsPage.total,
  );

  return (
    <div className="min-w-0 space-y-4 sm:-mx-2 sm:-my-2">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold font-fustat">Memory Exports</h1>
          <p className="mt-1 text-sm text-onSurface-default-secondary">
            Export memories in a structured format using customizable Pydantic
            schemas.{" "}
            <a
              href={`${process.env.NEXT_PUBLIC_API_URL || ""}/docs`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-onSurface-info-primary hover:underline"
            >
              Learn more
            </a>
          </p>
        </div>
        <div className="flex w-full shrink-0 items-center gap-2 sm:w-auto sm:self-auto">
          <Button
            variant="outline"
            className="flex-1 sm:flex-none"
            onClick={() => void loadJobs(page, debouncedSearch, true)}
            disabled={isRefreshing}
          >
            <RefreshCw
              className={`mr-2 size-4 ${isRefreshing ? "animate-spin" : ""}`}
            />
            Refresh
          </Button>
          <Button
            variant="primary"
            className="flex-1 sm:flex-none"
            onClick={() => setIsCreateOpen(true)}
          >
            <Plus className="mr-2 size-4" />
            Create Export
          </Button>
        </div>
      </div>

      <div className="relative w-full sm:max-w-96">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-onSurface-default-tertiary" />
        <Input
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setPage(1);
          }}
          placeholder="Search by ID or entity..."
          className="pl-9"
          aria-label="Search memory exports"
        />
      </div>

      <div className="min-w-0 overflow-hidden rounded-lg border border-memBorder-primary bg-surface-default-primary">
        {isLoading ? (
          <>
            <div className="hidden sm:block">
              <TableSkeleton rows={5} columns={6} />
            </div>
            <div className="sm:hidden">
              <TableSkeleton rows={5} columns={3} />
            </div>
          </>
        ) : (
          <>
            {jobsPage.items.length === 0 ? (
              <>
                <div className="h-[38px] overflow-hidden">
                  <div className="hidden sm:block">
                    <DataTable
                      data={[]}
                      columns={columns}
                      getRowKey={(row) => row.id}
                    />
                  </div>
                  <div className="sm:hidden">
                    <DataTable
                      data={[]}
                      columns={mobileColumns}
                      getRowKey={(row) => row.id}
                    />
                  </div>
                </div>
                <div className="flex h-32 flex-col items-center justify-center gap-2 border-t border-memBorder-primary text-center">
                  <FileOutput className="size-8 text-memNeutral-400" />
                  <p className="text-sm text-onSurface-default-secondary">
                    {debouncedSearch
                      ? "No matching memory exports found"
                      : "No memory exports found"}
                  </p>
                </div>
              </>
            ) : (
              <>
                <div className="hidden sm:block">
                  <DataTable
                    data={jobsPage.items}
                    columns={columns}
                    getRowKey={(row) => row.id}
                  />
                </div>
                <div className="sm:hidden">
                  <DataTable
                    data={jobsPage.items}
                    columns={mobileColumns}
                    getRowKey={(row) => row.id}
                  />
                </div>
              </>
            )}
          </>
        )}
      </div>

      {!isLoading && jobsPage.total > 0 && (
        <div className="flex items-center justify-between gap-3 text-sm text-onSurface-default-tertiary">
          <span>
            {firstVisible}-{lastVisible} of {jobsPage.total}
          </span>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="size-8"
              disabled={!jobsPage.has_previous}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              aria-label="Previous page"
              title="Previous page"
            >
              <ChevronLeft className="size-4" />
            </Button>
            <span className="min-w-20 text-center text-onSurface-default-secondary">
              Page {jobsPage.page} of {Math.max(jobsPage.total_pages, 1)}
            </span>
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="size-8"
              disabled={!jobsPage.has_next}
              onClick={() => setPage((current) => current + 1)}
              aria-label="Next page"
              title="Next page"
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      )}

      <DialogPrimitive.Root
        open={isCreateOpen}
        onOpenChange={(open) => {
          if (creating) return;
          setIsCreateOpen(open);
          if (!open) resetBuilder();
        }}
      >
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/30 data-[state=closed]:animate-out data-[state=open]:animate-in data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 motion-reduce:animate-none" />
          <DialogPrimitive.Content className="fixed inset-y-0 right-0 z-50 flex w-full flex-col overflow-hidden border-l border-memBorder-primary bg-surface-default-primary shadow-xl outline-none data-[state=closed]:animate-out data-[state=open]:animate-in data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right motion-reduce:animate-none sm:w-[min(720px,50vw)]">
            <div className="flex h-12 shrink-0 items-center justify-between border-b border-memBorder-primary px-3">
              <DialogPrimitive.Title className="text-lg font-semibold">
                Create Memory Export
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="sr-only">
                Configure memory export filters, dates, and output schema.
              </DialogPrimitive.Description>
              <DialogPrimitive.Close asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-8"
                  aria-label="Close create export"
                  title="Close"
                >
                  <X className="size-4" />
                </Button>
              </DialogPrimitive.Close>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-4">
              <div className="inline-flex h-8 rounded-md bg-surface-default-secondary">
                <button
                  type="button"
                  onClick={() => setMode("visual")}
                  className={`flex h-8 items-center gap-1.5 rounded px-2 text-xs font-medium transition-colors ${
                    mode === "visual"
                      ? "border border-memBorder-primary bg-surface-default-primary text-onSurface-default-primary shadow-sm"
                      : "text-onSurface-default-secondary"
                  }`}
                  aria-pressed={mode === "visual"}
                >
                  <Layers3 className="size-3.5" />
                  Visual
                </button>
                <button
                  type="button"
                  onClick={() => setMode("raw")}
                  className={`flex h-8 items-center gap-1.5 rounded px-2 text-xs font-medium transition-colors ${
                    mode === "raw"
                      ? "border border-memBorder-primary bg-surface-default-primary text-onSurface-default-primary shadow-sm"
                      : "text-onSurface-default-secondary"
                  }`}
                  aria-pressed={mode === "raw"}
                >
                  <Code2 className="size-3.5" />
                  Raw
                </button>
              </div>

              <div className="mt-2 space-y-4">
                {mode === "visual" ? (
                  <div className="space-y-2">
                    <Label className="text-xs font-medium text-onSurface-default-secondary">
                      Entity Filters
                    </Label>
                    <div className="space-y-2">
                      {filters.map((filter) => (
                        <div key={filter.id} className="flex min-w-0 gap-2">
                          <Select
                            value={filter.type}
                            onValueChange={(value: EntityType) =>
                              updateFilter(filter.id, { type: value })
                            }
                          >
                            <SelectTrigger
                              variant="dropdown"
                              className="h-8 w-28 shrink-0"
                              aria-label="Entity type"
                            >
                              <UserRound className="mr-2 size-4 shrink-0" />
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {ENTITY_TYPES.map((entityType) => (
                                <SelectItem
                                  key={entityType.value}
                                  value={entityType.value}
                                >
                                  {entityType.label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <Input
                            value={filter.value}
                            onChange={(event) =>
                              updateFilter(filter.id, {
                                value: event.target.value,
                              })
                            }
                            placeholder="Enter ID..."
                            className="h-8 min-w-0 flex-1"
                            aria-label={`${entityLabel(filter.type, language)} ID`}
                          />
                          {filters.length > 1 && (
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="size-9 shrink-0"
                              onClick={() =>
                                setFilters((current) =>
                                  current.filter(
                                    (item) => item.id !== filter.id,
                                  ),
                                )
                              }
                              aria-label="Remove entity filter"
                              title="Remove filter"
                            >
                              <Trash2 className="size-4" />
                            </Button>
                          )}
                        </div>
                      ))}
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-6 px-2"
                      onClick={addFilter}
                    >
                      <Plus className="mr-1.5 size-3.5" />
                      Add Filter
                    </Button>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label
                        htmlFor="raw-export-filters"
                        className="text-xs font-medium text-onSurface-default-secondary"
                      >
                        Raw Filters
                      </Label>
                      {!rawFiltersAreValid && (
                        <span className="text-xs text-onSurface-danger-primary">
                          Invalid JSON object
                        </span>
                      )}
                    </div>
                    <Textarea
                      id="raw-export-filters"
                      value={rawFilters}
                      onChange={(event) => setRawFilters(event.target.value)}
                      className="min-h-36 resize-y font-mono text-xs"
                      spellCheck={false}
                    />
                  </div>
                )}

                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs font-medium text-onSurface-default-secondary">
                      Date Range (Optional)
                    </Label>
                    {dateRange?.from && (
                      <button
                        type="button"
                        className="text-xs text-onSurface-default-tertiary hover:text-onSurface-default-primary"
                        onClick={() => setDateRange(undefined)}
                      >
                        Clear
                      </button>
                    )}
                  </div>
                  <Popover>
                    <PopoverTrigger asChild>
                      <Button
                        type="button"
                        variant="outline"
                        className="w-full justify-start px-3 font-normal"
                      >
                        <CalendarDays className="mr-2 size-4 text-onSurface-default-tertiary" />
                        {dateRange?.from ? (
                          dateRange.to ? (
                            `${format(dateRange.from, "MMM d, yyyy", { locale: dateLocale })} - ${format(
                              dateRange.to,
                              "MMM d, yyyy",
                              { locale: dateLocale },
                            )}`
                          ) : (
                            format(dateRange.from, "MMM d, yyyy", {
                              locale: dateLocale,
                            })
                          )
                        ) : (
                          <span className="text-onSurface-default-tertiary">
                            Pick a date range
                          </span>
                        )}
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent
                      align="start"
                      className="w-auto max-w-[calc(100vw-2rem)] overflow-auto rounded-md p-0"
                    >
                      <Calendar
                        mode="range"
                        selected={dateRange}
                        onSelect={setDateRange}
                        numberOfMonths={2}
                        defaultMonth={subMonths(new Date(), 1)}
                        disabled={{ after: new Date() }}
                        initialFocus
                      />
                    </PopoverContent>
                  </Popover>
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label
                      htmlFor="export-schema"
                      className="text-xs font-medium text-onSurface-default-secondary"
                    >
                      Pydantic Schema
                    </Label>
                    {!schemaIsValid && (
                      <span className="text-xs text-onSurface-danger-primary">
                        Invalid JSON object
                      </span>
                    )}
                  </div>
                  <Textarea
                    id="export-schema"
                    value={schema}
                    onChange={(event) => setSchema(event.target.value)}
                    className="min-h-[198px] max-h-[45vh] resize-y font-mono text-xs"
                    spellCheck={false}
                  />
                  <p className="text-[11px] text-onSurface-default-tertiary">
                    Drag the bottom edge to resize
                  </p>
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <DialogPrimitive.Close asChild>
                    <Button type="button" variant="outline" disabled={creating}>
                      Cancel
                    </Button>
                  </DialogPrimitive.Close>
                  <Button
                    type="button"
                    variant="primary"
                    onClick={() => void createExport()}
                    disabled={!canCreate || creating}
                  >
                    {creating ? "Creating..." : "Create Export"}
                  </Button>
                </div>
              </div>
            </div>
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
    </div>
  );
}
