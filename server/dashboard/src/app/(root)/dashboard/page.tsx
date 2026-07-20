"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  CalendarDays,
  CircleHelp,
  Database,
  ExternalLink,
  MessageSquare,
  Search,
  SlidersHorizontal,
  Sparkles,
  Users,
} from "lucide-react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { format, parseISO, type Locale } from "date-fns";
import { enUS, zhCN } from "date-fns/locale";
import type { DateRange } from "react-day-picker";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Card, CardContent } from "@/components/ui/card";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useApiQuery } from "@/hooks/use-api-query";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { Entity, UsageSummary } from "@/types/api";
import { api } from "@/utils/api";
import { ENTITY_ENDPOINTS, USAGE_ENDPOINTS } from "@/utils/api-endpoints";

type RangePreset = "all" | "1" | "7" | "30" | "custom";

type DashboardPoint = UsageSummary["series"][number] & {
  label: string;
  other_requests: number;
  total_entities: number;
  users: number;
  agents: number;
  apps: number;
  runs: number;
};

const REQUEST_COLORS = {
  total: "#6d5dfc",
  writes: "#0f766e",
  searches: "#2563eb",
  other: "#ca8a04",
} as const;

const ENTITY_COLORS = {
  total: "#6d5dfc",
  users: "#2563eb",
  agents: "#0f766e",
  apps: "#ca8a04",
  runs: "#dc2626",
} as const;

function formatMetric(value: number, language: "en" | "zh") {
  const locale = language === "zh" ? "zh-CN" : "en-US";
  return value >= 1000
    ? new Intl.NumberFormat(locale, {
        notation: "compact",
        maximumFractionDigits: 1,
      }).format(value)
    : value.toLocaleString(locale);
}

function rangeParams(preset: RangePreset, range?: DateRange) {
  if (preset === "all") return { all_time: true };
  if (preset === "custom" && range?.from && range.to) {
    return {
      start_date: format(range.from, "yyyy-MM-dd"),
      end_date: format(range.to, "yyyy-MM-dd"),
    };
  }
  return { days: Number(preset) || 30 };
}

function buildDashboardSeries(
  summary: UsageSummary | undefined,
  entities: Entity[],
  locale: Locale,
): DashboardPoint[] {
  if (!summary) return [];

  const sortedEntities = entities
    .map((entity) => ({
      type: entity.type,
      date: entity.created_at?.slice(0, 10) ?? "",
    }))
    .sort((a, b) => a.date.localeCompare(b.date));
  const counts = { users: 0, agents: 0, apps: 0, runs: 0 };
  let cursor = 0;

  return summary.series.map((point) => {
    while (
      cursor < sortedEntities.length &&
      (!sortedEntities[cursor].date ||
        sortedEntities[cursor].date <= point.date)
    ) {
      const type = sortedEntities[cursor].type;
      if (type === "user") counts.users += 1;
      if (type === "agent") counts.agents += 1;
      if (type === "app") counts.apps += 1;
      if (type === "run") counts.runs += 1;
      cursor += 1;
    }

    const totalEntities =
      counts.users + counts.agents + counts.apps + counts.runs;
    return {
      ...point,
      label: format(
        parseISO(point.date),
        summary.period.days > 180 ? "MMM yy" : "MMM d",
        { locale },
      ),
      other_requests: Math.max(
        0,
        point.api_requests - point.memory_writes - point.memory_searches,
      ),
      total_entities: totalEntities,
      ...counts,
    };
  });
}

export default function DashboardPage() {
  const { language } = useI18n();
  const dateLocale = language === "zh" ? zhCN : enUS;
  const [preset, setPreset] = useState<RangePreset>("7");
  const [dateRange, setDateRange] = useState<DateRange>();
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [requestBreakdown, setRequestBreakdown] = useState(false);
  const [entityBreakdown, setEntityBreakdown] = useState(false);

  const {
    data: summary,
    isLoading: summaryLoading,
    error: summaryError,
    refetch: refetchSummary,
  } = useApiQuery<UsageSummary>(
    async () =>
      (
        await api.get<UsageSummary>(USAGE_ENDPOINTS.SUMMARY, {
          params: rangeParams(preset, dateRange),
        })
      ).data,
    { errorToast: "Failed to load dashboard usage" },
  );

  const {
    data: entities = [],
    isLoading: entitiesLoading,
    error: entitiesError,
    refetch: refetchEntities,
  } = useApiQuery<Entity[]>(
    async () => (await api.get<Entity[]>(ENTITY_ENDPOINTS.BASE)).data ?? [],
    { errorToast: "Failed to load dashboard entities", initialData: [] },
  );

  useEffect(() => {
    void refetchSummary();
  }, [preset, dateRange?.from, dateRange?.to, refetchSummary]);

  useEffect(() => {
    const refresh = () => {
      void refetchSummary();
      void refetchEntities();
    };
    window.addEventListener("yiqiao-projects-updated", refresh);
    return () => window.removeEventListener("yiqiao-projects-updated", refresh);
  }, [refetchEntities, refetchSummary]);

  const chartData = useMemo(
    () => buildDashboardSeries(summary, entities, dateLocale),
    [dateLocale, entities, summary],
  );
  const loading = summaryLoading || entitiesLoading;
  const retrievalPolicy = summary?.effective_limits.find(
    (policy) => policy.metric === "memory_searches",
  );
  const totalEntities = chartData.at(-1)?.total_entities ?? entities.length;

  const rangeDescription = useMemo(() => {
    if (preset === "all") return "all time";
    if (preset === "1") return "1 day";
    if (preset === "7") return "7 days";
    if (preset === "30") return "30 days";
    return dateRange?.from && dateRange.to
      ? `${format(dateRange.from, "MMM d", { locale: dateLocale })} - ${format(dateRange.to, "MMM d", { locale: dateLocale })}`
      : "selected range";
  }, [dateLocale, dateRange, preset]);

  const metrics = [
    {
      label: "Total Memories",
      value: formatMetric(summary?.totals.stored_memories ?? 0, language),
      detail: "Memories currently stored in this project.",
      icon: Database,
      tone: "bg-violet-50 text-violet-700 dark:bg-violet-950/40 dark:text-violet-300",
    },
    {
      label: `Retrieval API Usage (${rangeDescription})`,
      value: retrievalPolicy
        ? `${Math.round(retrievalPolicy.percent ?? 0)}%`
        : "Unlimited",
      detail: "Retrieval events as a percentage of the active project limit.",
      icon: Search,
      tone: "bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300",
    },
    {
      label: "Retrieval Events",
      value: formatMetric(summary?.totals.memory_searches ?? 0, language),
      detail: `Successful memory retrievals across ${rangeDescription}.`,
      icon: Sparkles,
      tone: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300",
    },
    {
      label: "Add Events",
      value: formatMetric(summary?.totals.memory_writes ?? 0, language),
      detail: `Successful memory writes across ${rangeDescription}.`,
      icon: SlidersHorizontal,
      tone: "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
    },
  ];

  const applyPreset = (nextPreset: Exclude<RangePreset, "custom">) => {
    setPreset(nextPreset);
    setDateRange(undefined);
  };

  const selectDateRange = (nextRange: DateRange | undefined) => {
    setDateRange(nextRange);
    if (nextRange?.from && nextRange.to) {
      setPreset("custom");
      setCalendarOpen(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-[1440px] space-y-6 pb-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <Popover open={calendarOpen} onOpenChange={setCalendarOpen}>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              className={cn(
                "w-full justify-between font-normal sm:w-auto sm:min-w-[220px]",
                preset === "custom" && "border-onSurface-default-primary",
              )}
            >
              <span className="truncate">
                {dateRange?.from && dateRange.to
                  ? `${format(dateRange.from, "MMM d, yyyy", { locale: dateLocale })} - ${format(dateRange.to, "MMM d, yyyy", { locale: dateLocale })}`
                  : "Pick a date range"}
              </span>
              <CalendarDays className="ml-3 size-4 shrink-0 text-onSurface-default-tertiary" />
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-auto rounded-lg p-0">
            <Calendar
              initialFocus
              mode="range"
              defaultMonth={dateRange?.from}
              selected={dateRange}
              onSelect={selectDateRange}
              numberOfMonths={1}
              disabled={{ after: new Date() }}
            />
          </PopoverContent>
        </Popover>

        <div
          className="grid h-9 grid-cols-4 rounded-md border border-memBorder-primary bg-surface-default-secondary p-0.5 sm:flex sm:self-auto"
          aria-label="Dashboard date range"
        >
          {[
            ["all", language === "zh" ? "全部时间" : "All Time"],
            ["1", language === "zh" ? "1 天" : "1d"],
            ["7", language === "zh" ? "7 天" : "7d"],
            ["30", language === "zh" ? "30 天" : "30d"],
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() =>
                applyPreset(value as Exclude<RangePreset, "custom">)
              }
              className={cn(
                "h-8 min-w-16 rounded px-3 text-xs font-semibold text-onSurface-default-secondary transition-colors",
                preset === value
                  ? "bg-surface-default-primary text-onSurface-default-primary shadow-sm"
                  : "hover:bg-surface-default-tertiary-hover",
              )}
              aria-pressed={preset === value}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {(summaryError || entitiesError) && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200">
          {summaryError || entitiesError}
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {metrics.map((metric) => (
          <Card key={metric.label} className="min-h-[132px] rounded-lg">
            <CardContent className="flex h-full items-start justify-between gap-4 p-5">
              <div className="min-w-0">
                <div className="flex min-h-10 items-start gap-1.5 text-sm leading-5 text-onSurface-default-secondary">
                  <metric.icon className="mt-0.5 size-4 shrink-0" />
                  <span className="min-w-0 break-words">{metric.label}</span>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        aria-label={`About ${metric.label}`}
                        className="mt-0.5 shrink-0 text-onSurface-default-tertiary"
                      >
                        <CircleHelp className="size-3.5" />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-64">
                      {metric.detail}
                    </TooltipContent>
                  </Tooltip>
                </div>
                {loading ? (
                  <Skeleton className="mt-5 h-9 w-24" />
                ) : (
                  <p className="mt-4 text-3xl font-semibold tabular-nums">
                    {metric.value}
                  </p>
                )}
              </div>
              <div
                className={cn(
                  "flex size-9 shrink-0 items-center justify-center rounded-md",
                  metric.tone,
                )}
              >
                <metric.icon className="size-4" />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <ActivityChart
          title="Requests"
          total={summary?.totals.api_requests ?? 0}
          href="/dashboard/requests"
          linkLabel="View Requests"
          data={chartData}
          loading={loading}
          breakdown={requestBreakdown}
          onBreakdownChange={setRequestBreakdown}
          type="requests"
        />
        <ActivityChart
          title="Entities"
          total={totalEntities}
          href="/dashboard/entities"
          linkLabel="View Entities"
          data={chartData}
          loading={loading}
          breakdown={entityBreakdown}
          onBreakdownChange={setEntityBreakdown}
          type="entities"
        />
      </div>

      <section className="space-y-4 pt-2">
        <div>
          <h2 className="text-lg font-semibold">Explore the Platform</h2>
          <p className="mt-1 text-sm text-onSurface-default-secondary">
            Get the most out of YiQiao: tune it, see it in action, and ship
            faster.
          </p>
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
          <ExploreCard
            href="/dashboard/settings/extraction"
            title="Customize YiQiao"
            description="Set what YiQiao remembers, how it is organized, and when it is used."
            action="Try it"
            icon={SlidersHorizontal}
            suggested
          />
          <ExploreCard
            href="/dashboard/install"
            title="Integration Examples"
            description="See real integration examples and patterns to add memory to your product."
            action="Open"
            icon={Sparkles}
          />
          <ExploreCard
            href="/playground"
            title="Try the Playground"
            description="Test memory addition and retrieval live before wiring it into your app."
            action="Try it"
            icon={MessageSquare}
          />
          <ExploreCard
            href="/dashboard/install"
            title="Quick Start"
            description="Use tested examples to add and search memories from your application."
            action="View"
            icon={BookOpen}
          />
        </div>
      </section>
    </div>
  );
}

function ActivityChart({
  title,
  total,
  href,
  linkLabel,
  data,
  loading,
  breakdown,
  onBreakdownChange,
  type,
}: {
  title: string;
  total: number;
  href: string;
  linkLabel: string;
  data: DashboardPoint[];
  loading: boolean;
  breakdown: boolean;
  onBreakdownChange: (value: boolean) => void;
  type: "requests" | "entities";
}) {
  const { language } = useI18n();
  return (
    <Card className="overflow-hidden rounded-lg">
      <CardContent className="p-0">
        <div className="flex items-center justify-between gap-4 px-5 pb-3 pt-5">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold">{title}</h3>
            {loading ? (
              <Skeleton className="mt-2 h-6 w-20" />
            ) : (
              <p className="mt-1 text-xl font-semibold tabular-nums">
                {formatMetric(total, language)}
              </p>
            )}
          </div>
          <Button asChild variant="outline" size="sm">
            <Link href={href}>
              {linkLabel}
              <ArrowRight className="ml-1.5 size-3.5" />
            </Link>
          </Button>
        </div>

        <div className="h-[270px] w-full px-2">
          {loading ? (
            <Skeleton className="h-full w-full" />
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart
                data={data}
                margin={{ top: 12, right: 14, bottom: 4, left: -18 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  vertical={false}
                  opacity={0.22}
                />
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 11 }}
                  minTickGap={28}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  allowDecimals={false}
                  tick={{ fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={48}
                />
                <ChartTooltip
                  contentStyle={{
                    borderRadius: 8,
                    borderColor: "var(--mem-border-primary)",
                    fontSize: 12,
                  }}
                />
                {breakdown && <Legend wrapperStyle={{ fontSize: 11 }} />}
                {type === "requests" ? (
                  breakdown ? (
                    <>
                      <Line
                        type="monotone"
                        dataKey="memory_writes"
                        name="Add"
                        stroke={REQUEST_COLORS.writes}
                        strokeWidth={2}
                        dot={false}
                      />
                      <Line
                        type="monotone"
                        dataKey="memory_searches"
                        name="Retrieval"
                        stroke={REQUEST_COLORS.searches}
                        strokeWidth={2}
                        dot={false}
                      />
                      <Line
                        type="monotone"
                        dataKey="other_requests"
                        name="Other"
                        stroke={REQUEST_COLORS.other}
                        strokeWidth={2}
                        dot={false}
                      />
                    </>
                  ) : (
                    <Area
                      type="monotone"
                      dataKey="api_requests"
                      name="Requests"
                      stroke={REQUEST_COLORS.total}
                      fill={REQUEST_COLORS.total}
                      fillOpacity={0.1}
                      strokeWidth={2}
                      dot={false}
                    />
                  )
                ) : breakdown ? (
                  <>
                    <Line
                      type="monotone"
                      dataKey="users"
                      name="Users"
                      stroke={ENTITY_COLORS.users}
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="agents"
                      name="Agents"
                      stroke={ENTITY_COLORS.agents}
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="apps"
                      name="Apps"
                      stroke={ENTITY_COLORS.apps}
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="runs"
                      name="Runs"
                      stroke={ENTITY_COLORS.runs}
                      strokeWidth={2}
                      dot={false}
                    />
                  </>
                ) : (
                  <Area
                    type="monotone"
                    dataKey="total_entities"
                    name="Entities"
                    stroke={ENTITY_COLORS.total}
                    fill={ENTITY_COLORS.total}
                    fillOpacity={0.1}
                    strokeWidth={2}
                    dot={false}
                  />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </div>

        <div className="flex min-h-12 items-center justify-between border-t border-memBorder-primary px-5 py-3">
          <span className="text-[11px] font-semibold uppercase text-onSurface-default-tertiary">
            {language === "zh"
              ? `${title === "Requests" ? "请求" : "实体"}总数`
              : `Total ${title}`}
          </span>
          <label className="flex items-center gap-2 text-xs text-onSurface-default-secondary">
            View Breakdown
            <Switch
              checked={breakdown}
              onCheckedChange={onBreakdownChange}
              aria-label={`View ${title.toLowerCase()} breakdown`}
            />
          </label>
        </div>
      </CardContent>
    </Card>
  );
}

function ExploreCard({
  href,
  title,
  description,
  action,
  icon: Icon,
  suggested = false,
  external = false,
}: {
  href: string;
  title: string;
  description: string;
  action: string;
  icon: typeof Users;
  suggested?: boolean;
  external?: boolean;
}) {
  return (
    <Link
      href={href}
      target={external ? "_blank" : undefined}
      rel={external ? "noreferrer" : undefined}
      className="group relative flex min-h-[190px] flex-col rounded-lg border border-memBorder-primary bg-surface-default-primary p-5 transition-colors hover:bg-surface-default-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex size-9 items-center justify-center rounded-md bg-surface-default-tertiary text-onSurface-default-primary">
          <Icon className="size-4" />
        </div>
        {suggested && (
          <span className="rounded bg-violet-50 px-2 py-1 text-[10px] font-semibold text-violet-700 dark:bg-violet-950/40 dark:text-violet-300">
            Suggested
          </span>
        )}
      </div>
      <h3 className="mt-4 text-sm font-semibold">{title}</h3>
      <p className="mt-2 flex-1 text-sm leading-5 text-onSurface-default-secondary">
        {description}
      </p>
      <span className="mt-4 flex items-center gap-1.5 text-xs font-semibold text-onSurface-default-primary">
        {action}
        {external ? (
          <ExternalLink className="size-3.5" />
        ) : (
          <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
        )}
      </span>
    </Link>
  );
}
