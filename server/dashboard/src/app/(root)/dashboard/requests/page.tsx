// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { useEffect, useRef, useState } from "react";
import { format, subDays } from "date-fns";
import { enUS, zhCN } from "date-fns/locale";
import {
  CirclePlus,
  Info,
  ListFilter,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Table2,
} from "lucide-react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { TableSkeleton } from "@/components/shared/table-skeleton";
import { EmptyState } from "@/components/self-hosted/empty-state";
import {
  RequestDetailSheet,
  RequestLogTable,
} from "@/components/requests/request-activity";
import { cn } from "@/lib/utils";
import { api } from "@/utils/api";
import { REQUEST_ENDPOINTS } from "@/utils/api-endpoints";
import { useApiQuery } from "@/hooks/use-api-query";
import { useI18n } from "@/lib/i18n";
import { ApiRequestLog, EntityType, RequestLogPage } from "@/types/api";

const PAGE_SIZE = 20;

type ActivityView = "overview" | "ADD" | "SEARCH" | "GET_ALL" | "results";
type RangeKey =
  | "1h"
  | "6h"
  | "12h"
  | "1d"
  | "7d"
  | "14d"
  | "30d"
  | "90d"
  | "all"
  | "custom";

const RANGE_OPTIONS: { value: RangeKey; label: string; hours?: number }[] = [
  { value: "1h", label: "Last hour", hours: 1 },
  { value: "6h", label: "Last 6 hours", hours: 6 },
  { value: "12h", label: "Last 12 hours", hours: 12 },
  { value: "1d", label: "Last day", hours: 24 },
  { value: "7d", label: "Last 7 days", hours: 24 * 7 },
  { value: "14d", label: "Last 14 days", hours: 24 * 14 },
  { value: "30d", label: "Last 30 days", hours: 24 * 30 },
  { value: "90d", label: "Last 90 days", hours: 24 * 90 },
  { value: "all", label: "All time" },
  { value: "custom", label: "Custom" },
];

const VIEW_OPTIONS: {
  value: ActivityView;
  label: string;
  icon: React.ElementType;
}[] = [
  { value: "overview", label: "Overview", icon: Table2 },
  { value: "ADD", label: "ADD", icon: CirclePlus },
  { value: "SEARCH", label: "SEARCH", icon: Search },
  { value: "GET_ALL", label: "GET ALL", icon: ListFilter },
  { value: "results", label: "Has Results", icon: ListFilter },
];

type RequestFilters = {
  entityType: EntityType;
  entityId: string;
  status: "all" | "succeeded" | "failed";
};

const EMPTY_FILTERS: RequestFilters = {
  entityType: "user",
  entityId: "",
  status: "all",
};

export default function RequestsPage() {
  const { language } = useI18n();
  const dateLocale = language === "zh" ? zhCN : enUS;
  const [page, setPage] = useState(1);
  const [view, setView] = useState<ActivityView>("overview");
  const [range, setRange] = useState<RangeKey>("all");
  const [customStart, setCustomStart] = useState(
    format(subDays(new Date(), 7), "yyyy-MM-dd"),
  );
  const [customEnd, setCustomEnd] = useState(format(new Date(), "yyyy-MM-dd"));
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [draftFilters, setDraftFilters] =
    useState<RequestFilters>(EMPTY_FILTERS);
  const [filters, setFilters] = useState<RequestFilters>(EMPTY_FILTERS);
  const [selectedLog, setSelectedLog] = useState<ApiRequestLog | null>(null);
  const didMount = useRef(false);

  const {
    data = { items: [], total: 0, page: 1, page_size: PAGE_SIZE, series: [] },
    isLoading,
    error,
    refetch,
  } = useApiQuery<RequestLogPage>(
    async () => {
      const params: Record<string, string | number | boolean> = {
        page,
        page_size: PAGE_SIZE,
      };
      if (view === "results") params.has_results = true;
      else if (view !== "overview") params.event_type = view;

      const selectedRange = RANGE_OPTIONS.find((item) => item.value === range);
      if (selectedRange?.hours) {
        params.start_at = new Date(
          Date.now() - selectedRange.hours * 60 * 60 * 1000,
        ).toISOString();
      } else if (range === "custom") {
        if (customStart) {
          params.start_at = new Date(`${customStart}T00:00:00`).toISOString();
        }
        if (customEnd) {
          params.end_at = new Date(`${customEnd}T23:59:59.999`).toISOString();
        }
      }
      if (filters.entityId.trim()) {
        params.entity_type = filters.entityType;
        params.entity_id = filters.entityId.trim();
      }
      if (filters.status !== "all") {
        params.succeeded = filters.status === "succeeded";
      }
      const response = await api.get<RequestLogPage>(REQUEST_ENDPOINTS.BASE, {
        params,
      });
      return response.data;
    },
    {
      errorToast: "Failed to load request activity",
      initialData: {
        items: [],
        total: 0,
        page: 1,
        page_size: PAGE_SIZE,
        series: [],
      },
    },
  );

  useEffect(() => {
    if (didMount.current) void refetch();
    else didMount.current = true;
  }, [customEnd, customStart, filters, page, range, refetch, view]);

  const seriesSpan = data.series.length
    ? new Date(data.series[data.series.length - 1].bucket).getTime() -
      new Date(data.series[0].bucket).getTime()
    : 0;
  const chartData = data.series.map((point) => ({
    ...point,
    label: format(
      new Date(point.bucket),
      seriesSpan <= 48 * 60 * 60 * 1000 ? "MMM d HH:mm" : "MMM d",
      { locale: dateLocale },
    ),
  }));
  const totalPages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  const activeFilterCount =
    Number(Boolean(filters.entityId)) + Number(filters.status !== "all");

  return (
    <div className="min-w-0 space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-fustat text-xl font-semibold">Requests</h1>
        <Select
          value={range}
          onValueChange={(value) => {
            setPage(1);
            setRange(value as RangeKey);
          }}
        >
          <SelectTrigger variant="dropdown" className="h-9 w-[144px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {RANGE_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {range === "custom" && (
        <div className="flex flex-wrap justify-end gap-2 border-y border-memBorder-primary bg-surface-default-secondary p-3">
          <Input
            type="date"
            aria-label="Custom range start"
            value={customStart}
            max={customEnd || undefined}
            className="w-[160px]"
            onChange={(event) => {
              setPage(1);
              setCustomStart(event.target.value);
            }}
          />
          <Input
            type="date"
            aria-label="Custom range end"
            value={customEnd}
            min={customStart || undefined}
            className="w-[160px]"
            onChange={(event) => {
              setPage(1);
              setCustomEnd(event.target.value);
            }}
          />
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex max-w-full flex-wrap gap-2 pb-1">
          {VIEW_OPTIONS.map((option) => {
            const Icon = option.icon;
            return (
              <Button
                key={option.value}
                variant="outline"
                className={cn(
                  "h-9 shrink-0 gap-2 px-3 font-normal",
                  view === option.value &&
                    "border-onSurface-default-primary bg-surface-default-secondary",
                )}
                onClick={() => {
                  setPage(1);
                  setView(option.value);
                }}
              >
                <Icon className="size-4" />
                {option.label}
              </Button>
            );
          })}
          <Info className="mt-2 size-4 shrink-0 text-onSurface-default-tertiary" />
        </div>

        <div className="flex gap-2">
          <Button
            variant="outline"
            className="h-9 gap-2"
            onClick={() => setFiltersOpen((open) => !open)}
          >
            <SlidersHorizontal className="size-4" />
            Filters
            {activeFilterCount > 0 && (
              <span className="flex size-5 items-center justify-center rounded-full bg-surface-default-brand text-xs text-white">
                {activeFilterCount}
              </span>
            )}
          </Button>
          <Button
            variant="outline"
            className="h-9 gap-2"
            disabled={isLoading}
            onClick={() => void refetch()}
          >
            <RefreshCw className={cn("size-4", isLoading && "animate-spin")} />
            Refresh
          </Button>
        </div>
      </div>

      {filtersOpen && (
        <div className="grid gap-3 border-y border-memBorder-primary bg-surface-default-secondary p-3 sm:grid-cols-[140px_minmax(180px,1fr)_160px_auto_auto]">
          <Select
            value={draftFilters.entityType}
            onValueChange={(value) =>
              setDraftFilters((current) => ({
                ...current,
                entityType: value as EntityType,
              }))
            }
          >
            <SelectTrigger variant="dropdown" className="h-9 w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(["user", "agent", "run", "app"] as EntityType[]).map((type) => (
                <SelectItem key={type} value={type}>
                  {language === "zh"
                    ? {
                        user: "用户",
                        agent: "智能体",
                        run: "运行",
                        app: "应用",
                      }[type]
                    : type.toUpperCase()}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            value={draftFilters.entityId}
            placeholder="Entity ID"
            onChange={(event) =>
              setDraftFilters((current) => ({
                ...current,
                entityId: event.target.value,
              }))
            }
          />
          <Select
            value={draftFilters.status}
            onValueChange={(value) =>
              setDraftFilters((current) => ({
                ...current,
                status: value as RequestFilters["status"],
              }))
            }
          >
            <SelectTrigger variant="dropdown" className="h-9 w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Any status</SelectItem>
              <SelectItem value="succeeded">Succeeded</SelectItem>
              <SelectItem value="failed">Failed</SelectItem>
            </SelectContent>
          </Select>
          <Button
            className="h-9"
            onClick={() => {
              setPage(1);
              setFilters({ ...draftFilters });
            }}
          >
            Apply
          </Button>
          <Button
            variant="ghost"
            className="h-9"
            onClick={() => {
              setPage(1);
              setDraftFilters(EMPTY_FILTERS);
              setFilters(EMPTY_FILTERS);
            }}
          >
            Clear
          </Button>
        </div>
      )}

      <div className="h-[108px] border-b border-memBorder-primary px-2">
        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} barCategoryGap="20%">
              <XAxis
                dataKey="label"
                axisLine
                tickLine={false}
                interval="preserveStartEnd"
                tick={{ fontSize: 10, fill: "currentColor" }}
              />
              <Tooltip
                cursor={{ fill: "rgba(127, 127, 127, 0.08)" }}
                formatter={(value) => [value, "Requests"]}
                labelFormatter={(_label, payload) =>
                  payload[0]?.payload?.bucket
                    ? format(new Date(payload[0].payload.bucket), "PPpp", {
                        locale: dateLocale,
                      })
                    : ""
                }
              />
              <Bar dataKey="count" fill="#a78bfa" radius={[1, 1, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-full items-end border-b border-memBorder-primary pb-2 text-xs text-onSurface-default-tertiary">
            Request activity will appear here.
          </div>
        )}
      </div>

      {error && (
        <Card className="border-memBorder-primary">
          <CardContent className="p-4 text-sm text-onSurface-danger-primary">
            {error}
          </CardContent>
        </Card>
      )}

      {isLoading ? (
        <TableSkeleton rows={10} columns={6} />
      ) : data.items.length === 0 ? (
        <EmptyState
          title="No requests found"
          description="Request activity matching this view will appear here."
          image="requests"
        />
      ) : (
        <Card className="overflow-hidden border-memBorder-primary">
          <RequestLogTable
            logs={data.items}
            selectedId={selectedLog?.id}
            onSelect={setSelectedLog}
          />
        </Card>
      )}

      <div className="flex items-center justify-between text-sm text-onSurface-default-tertiary">
        <span>
          {data.total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1} -{" "}
          {Math.min(page * PAGE_SIZE, data.total)} of {data.total}
        </span>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1 || isLoading}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= totalPages || isLoading}
            onClick={() => setPage((current) => current + 1)}
          >
            Next
          </Button>
        </div>
      </div>

      <RequestDetailSheet
        log={selectedLog}
        onOpenChange={(open) => {
          if (!open) setSelectedLog(null);
        }}
      />
    </div>
  );
}
