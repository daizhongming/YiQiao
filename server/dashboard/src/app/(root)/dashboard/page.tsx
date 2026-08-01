// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import Link from "next/link";
import { useEffect, useId, useMemo, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  CalendarDays,
  CircleHelp,
  Database,
  MessageSquare,
  Search,
  SlidersHorizontal,
  Sparkles,
  Users,
} from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  Area,
  CartesianGrid,
  ComposedChart,
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

type ChartTooltipItem = {
  color?: string;
  dataKey?: string | number;
  name?: string | number;
  value?: string | number;
};

const REQUEST_COLORS = {
  total: "#806849",
  writes: "#16806f",
  searches: "#3977b8",
  other: "#b9792e",
} as const;

const ENTITY_COLORS = {
  total: "#806849",
  users: "#3977b8",
  agents: "#16806f",
  apps: "#b9792e",
  runs: "#bb5555",
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
      total_entities: counts.users + counts.agents + counts.apps + counts.runs,
      ...counts,
    };
  });
}

export default function DashboardPage() {
  const { language, t } = useI18n();
  const reduceMotion = useReducedMotion();
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
      label: t("Total Memories"),
      value: formatMetric(summary?.totals.stored_memories ?? 0, language),
      detail: t("Memories currently stored in this project."),
      icon: Database,
      tone: "memory",
    },
    {
      label: t(`Retrieval API Usage (${rangeDescription})`),
      value: retrievalPolicy
        ? `${Math.round(retrievalPolicy.percent ?? 0)}%`
        : t("Unlimited"),
      detail: t(
        "Retrieval events as a percentage of the active project limit.",
      ),
      icon: Search,
      tone: "usage",
    },
    {
      label: t("Retrieval Events"),
      value: formatMetric(summary?.totals.memory_searches ?? 0, language),
      detail: t(`Successful memory retrievals across ${rangeDescription}.`),
      icon: Sparkles,
      tone: "retrieval",
    },
    {
      label: t("Add Events"),
      value: formatMetric(summary?.totals.memory_writes ?? 0, language),
      detail: t(`Successful memory writes across ${rangeDescription}.`),
      icon: SlidersHorizontal,
      tone: "write",
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

  const enter = (delay = 0) => ({
    initial: reduceMotion ? false : { opacity: 0, y: 12 },
    animate: { opacity: 1, y: 0 },
    transition: {
      duration: reduceMotion ? 0 : 0.32,
      delay: reduceMotion ? 0 : delay,
      ease: [0.22, 1, 0.36, 1] as const,
    },
  });

  return (
    <div className="dashboard-overview mx-auto w-full max-w-[1440px] space-y-5 pb-4">
      <motion.header
        {...enter()}
        className="dashboard-overview-header flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between"
      >
        <div className="min-w-0">
          <p className="text-xs font-semibold text-onSurface-default-tertiary">
            {t("Workspace")}
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-onSurface-default-primary">
            {t("Overview")}
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-onSurface-default-secondary">
            {t("Monitor memory activity and project health at a glance.")}
          </p>
        </div>

        <div className="flex w-full flex-col-reverse gap-2 sm:flex-row sm:items-center lg:w-auto">
          <Popover open={calendarOpen} onOpenChange={setCalendarOpen}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                className={cn(
                  "dashboard-date-trigger h-10 w-full justify-between font-normal sm:w-auto sm:max-w-[300px] sm:min-w-[220px]",
                  preset === "custom" && "is-active",
                )}
              >
                <CalendarDays className="mr-2 size-4 shrink-0 text-onSurface-default-tertiary" />
                <span className="min-w-0 flex-1 truncate text-left">
                  {dateRange?.from && dateRange.to
                    ? `${format(dateRange.from, "MMM d, yyyy", { locale: dateLocale })} - ${format(dateRange.to, "MMM d, yyyy", { locale: dateLocale })}`
                    : t("Pick a date range")}
                </span>
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-auto rounded-lg p-0">
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
            className="dashboard-range-control grid h-10 w-full grid-cols-4 p-0.5 sm:w-auto"
            aria-label={t("Dashboard date range")}
          >
            {(
              [
                ["all", t("All Time")],
                ["1", t("1 day")],
                ["7", t("7 days")],
                ["30", t("30 days")],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => applyPreset(value)}
                className="relative h-9 min-w-0 rounded px-2 text-xs font-semibold text-onSurface-default-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:min-w-16 sm:px-3"
                aria-pressed={preset === value}
              >
                {preset === value && (
                  <motion.span
                    layoutId="dashboard-active-range"
                    className="absolute inset-0 rounded bg-surface-default-primary shadow-[var(--yiqiao-shadow-sm)]"
                    transition={{
                      duration: reduceMotion ? 0 : 0.22,
                      ease: [0.22, 1, 0.36, 1],
                    }}
                  />
                )}
                <span className="relative z-10 block truncate">{label}</span>
              </button>
            ))}
          </div>
        </div>
      </motion.header>

      {(summaryError || entitiesError) && (
        <div
          role="alert"
          className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200"
        >
          {summaryError || entitiesError}
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {metrics.map((metric, index) => (
          <motion.div key={metric.label} {...enter(0.05 + index * 0.045)}>
            <Card
              className="dashboard-metric-card h-[136px] overflow-hidden"
              data-tone={metric.tone}
            >
              <CardContent className="relative flex h-full items-start justify-between gap-4 p-5">
                <div className="min-w-0">
                  <div className="flex min-h-10 items-start gap-1.5 text-sm leading-5 text-onSurface-default-secondary">
                    <span className="min-w-0 break-words">{metric.label}</span>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          aria-label={t("Metric information")}
                          className="mt-0.5 shrink-0 rounded-sm text-onSurface-default-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          <CircleHelp className="size-3.5" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent className="max-w-64">
                        {metric.detail}
                      </TooltipContent>
                    </Tooltip>
                  </div>
                  <div className="mt-3 h-10 overflow-hidden">
                    {loading ? (
                      <Skeleton className="mt-1 h-8 w-24" />
                    ) : (
                      <AnimatePresence mode="wait" initial={false}>
                        <motion.p
                          key={metric.value}
                          initial={reduceMotion ? false : { opacity: 0, y: 8 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={
                            reduceMotion ? undefined : { opacity: 0, y: -8 }
                          }
                          transition={{ duration: reduceMotion ? 0 : 0.2 }}
                          className="break-words text-3xl font-semibold tabular-nums"
                        >
                          {metric.value}
                        </motion.p>
                      </AnimatePresence>
                    )}
                  </div>
                </div>
                <div className="dashboard-metric-icon flex size-10 shrink-0 items-center justify-center rounded-md">
                  <metric.icon className="size-4.5" />
                </div>
              </CardContent>
            </Card>
          </motion.div>
        ))}
      </div>

      <motion.div
        {...enter(0.22)}
        className="grid grid-cols-1 gap-4 xl:grid-cols-2"
      >
        <ActivityChart
          title={t("Requests")}
          total={summary?.totals.api_requests ?? 0}
          href="/dashboard/requests"
          linkLabel={t("View Requests")}
          data={chartData}
          loading={loading}
          breakdown={requestBreakdown}
          onBreakdownChange={setRequestBreakdown}
          type="requests"
        />
        <ActivityChart
          title={t("Entities")}
          total={totalEntities}
          href="/dashboard/entities"
          linkLabel={t("View Entities")}
          data={chartData}
          loading={loading}
          breakdown={entityBreakdown}
          onBreakdownChange={setEntityBreakdown}
          type="entities"
        />
      </motion.div>

      <motion.section {...enter(0.28)} className="space-y-3 pt-1">
        <div>
          <h2 className="text-base font-semibold">
            {t("Explore the Platform")}
          </h2>
          <p className="mt-1 text-sm text-onSurface-default-secondary">
            {t(
              "Get the most out of YiQiao: tune it, see it in action, and ship faster.",
            )}
          </p>
        </div>
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
          <ExploreCard
            href="/dashboard/settings/extraction"
            title={t("Customize YiQiao")}
            description={t(
              "Set what YiQiao remembers, how it is organized, and when it is used.",
            )}
            action={t("Try it")}
            icon={SlidersHorizontal}
            featured
          />
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-1">
            <ExploreCard
              href="/dashboard/install"
              title={t("Integration Examples")}
              action={t("Open")}
              icon={Sparkles}
            />
            <ExploreCard
              href="/playground"
              title={t("Try the Playground")}
              action={t("Try it")}
              icon={MessageSquare}
            />
            <ExploreCard
              href="/dashboard/install"
              title={t("Quick Start")}
              action={t("View")}
              icon={BookOpen}
            />
          </div>
        </div>
      </motion.section>
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
  const { language, t } = useI18n();
  const reduceMotion = useReducedMotion();
  const gradientId = `dashboard-${type}-${useId().replace(/:/g, "")}`;
  const series =
    type === "requests"
      ? [
          {
            key: "memory_writes",
            label: t("Add"),
            color: REQUEST_COLORS.writes,
          },
          {
            key: "memory_searches",
            label: t("Retrieval"),
            color: REQUEST_COLORS.searches,
          },
          {
            key: "other_requests",
            label: t("Other"),
            color: REQUEST_COLORS.other,
          },
        ]
      : [
          { key: "users", label: t("Users"), color: ENTITY_COLORS.users },
          { key: "agents", label: t("Agents"), color: ENTITY_COLORS.agents },
          { key: "apps", label: t("Apps"), color: ENTITY_COLORS.apps },
          { key: "runs", label: t("Runs"), color: ENTITY_COLORS.runs },
        ];
  const totalColor =
    type === "requests" ? REQUEST_COLORS.total : ENTITY_COLORS.total;
  const totalKey = type === "requests" ? "api_requests" : "total_entities";

  return (
    <Card className="dashboard-chart-card overflow-hidden">
      <CardContent className="p-0">
        <div className="flex min-h-[92px] flex-wrap items-start justify-between gap-3 px-4 pb-3 pt-4 sm:px-5 sm:pt-5">
          <div className="min-w-0">
            <h3 className="text-sm font-medium text-onSurface-default-secondary">
              {title}
            </h3>
            {loading ? (
              <Skeleton className="mt-2 h-7 w-20" />
            ) : (
              <p className="mt-1 text-2xl font-semibold tabular-nums">
                {formatMetric(total, language)}
              </p>
            )}
          </div>
          <Button asChild variant="ghost" size="sm" className="-mr-2">
            <Link href={href}>
              {linkLabel}
              <ArrowRight className="ml-1.5 size-3.5" />
            </Link>
          </Button>
          <AnimatePresence initial={false}>
            {breakdown && (
              <motion.div
                initial={reduceMotion ? false : { opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduceMotion ? undefined : { opacity: 0, y: -4 }}
                transition={{ duration: reduceMotion ? 0 : 0.18 }}
                className="flex w-full flex-wrap gap-x-4 gap-y-1"
                aria-label={t("Chart legend")}
              >
                {series.map((item) => (
                  <span
                    key={item.key}
                    className="flex items-center gap-1.5 text-[11px] text-onSurface-default-secondary"
                  >
                    <span
                      className="size-2 rounded-full"
                      style={{ backgroundColor: item.color }}
                    />
                    {item.label}
                  </span>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <div className="relative h-[252px] w-full px-2">
          {loading ? (
            <Skeleton className="h-full w-full rounded-none" />
          ) : data.length === 0 ? (
            <div className="grid h-full place-items-center text-sm text-onSurface-default-tertiary">
              {t("No activity in this range")}
            </div>
          ) : (
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={breakdown ? "breakdown" : "total"}
                initial={reduceMotion ? false : { opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={reduceMotion ? undefined : { opacity: 0 }}
                transition={{ duration: reduceMotion ? 0 : 0.2 }}
                className="h-full w-full"
              >
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart
                    data={data}
                    margin={{ top: 12, right: 14, bottom: 4, left: -18 }}
                  >
                    <defs>
                      <linearGradient
                        id={gradientId}
                        x1="0"
                        y1="0"
                        x2="0"
                        y2="1"
                      >
                        <stop
                          offset="0%"
                          stopColor={totalColor}
                          stopOpacity={0.3}
                        />
                        <stop
                          offset="100%"
                          stopColor={totalColor}
                          stopOpacity={0.02}
                        />
                      </linearGradient>
                    </defs>
                    <CartesianGrid
                      stroke="var(--mem-border-primary)"
                      vertical={false}
                      opacity={0.7}
                    />
                    <XAxis
                      dataKey="label"
                      tick={{
                        fontSize: 11,
                        fill: "var(--on-surface-default-tertiary)",
                      }}
                      minTickGap={28}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      allowDecimals={false}
                      tick={{
                        fontSize: 11,
                        fill: "var(--on-surface-default-tertiary)",
                      }}
                      axisLine={false}
                      tickLine={false}
                      width={48}
                    />
                    <ChartTooltip content={<DashboardChartTooltip />} />
                    {breakdown ? (
                      series.map((item) => (
                        <Line
                          key={item.key}
                          type="monotone"
                          dataKey={item.key}
                          name={item.label}
                          stroke={item.color}
                          strokeWidth={2}
                          dot={false}
                          activeDot={{ r: 3 }}
                          isAnimationActive={!reduceMotion}
                          animationDuration={420}
                        />
                      ))
                    ) : (
                      <Area
                        type="monotone"
                        dataKey={totalKey}
                        name={title}
                        stroke={totalColor}
                        fill={`url(#${gradientId})`}
                        strokeWidth={2.25}
                        dot={false}
                        activeDot={{ r: 3 }}
                        isAnimationActive={!reduceMotion}
                        animationDuration={420}
                      />
                    )}
                  </ComposedChart>
                </ResponsiveContainer>
              </motion.div>
            </AnimatePresence>
          )}
        </div>

        <div className="flex min-h-12 flex-wrap items-center justify-between gap-3 border-t border-memBorder-primary px-4 py-3 sm:px-5">
          <span className="text-[11px] font-semibold text-onSurface-default-tertiary">
            {t(type === "requests" ? "Total Requests" : "Total Entities")}
          </span>
          <label className="flex min-h-8 cursor-pointer items-center gap-2 text-xs text-onSurface-default-secondary">
            {t("View Breakdown")}
            <Switch
              checked={breakdown}
              onCheckedChange={onBreakdownChange}
              aria-label={t(
                type === "requests"
                  ? "View requests breakdown"
                  : "View entities breakdown",
              )}
            />
          </label>
        </div>
      </CardContent>
    </Card>
  );
}

function DashboardChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: ChartTooltipItem[];
  label?: string | number;
}) {
  if (!active || !payload?.length) return null;

  return (
    <div className="dashboard-chart-tooltip min-w-36 rounded-md border px-3 py-2 shadow-[var(--yiqiao-shadow-md)]">
      <p className="mb-1.5 text-xs font-medium text-onSurface-default-primary">
        {label}
      </p>
      <div className="space-y-1">
        {payload.map((item) => (
          <div
            key={String(item.dataKey)}
            className="flex items-center justify-between gap-5 text-xs"
          >
            <span className="flex items-center gap-1.5 text-onSurface-default-secondary">
              <span
                className="size-2 rounded-full"
                style={{ backgroundColor: item.color }}
              />
              {item.name}
            </span>
            <span className="font-semibold tabular-nums text-onSurface-default-primary">
              {item.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ExploreCard({
  href,
  title,
  description,
  action,
  icon: Icon,
  featured = false,
}: {
  href: string;
  title: string;
  description?: string;
  action: string;
  icon: typeof Users;
  featured?: boolean;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "dashboard-explore-card group relative flex border border-memBorder-primary bg-surface-default-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        featured
          ? "min-h-[168px] flex-col justify-between p-5 sm:p-6"
          : "min-h-[82px] items-center gap-3 p-4",
      )}
    >
      <div
        className={cn(
          "dashboard-explore-icon flex shrink-0 items-center justify-center rounded-md",
          featured ? "size-11" : "size-9",
        )}
      >
        <Icon className={featured ? "size-5" : "size-4"} />
      </div>
      <div className={cn("min-w-0", featured ? "mt-5" : "flex-1")}>
        <div className="flex items-center justify-between gap-3">
          <h3
            className={cn("font-semibold", featured ? "text-base" : "text-sm")}
          >
            {title}
          </h3>
          {!featured && (
            <ArrowRight className="size-4 shrink-0 text-onSurface-default-tertiary transition-transform group-hover:translate-x-0.5" />
          )}
        </div>
        {description && (
          <p className="mt-2 max-w-xl text-sm leading-5 text-onSurface-default-secondary">
            {description}
          </p>
        )}
        {featured && (
          <span className="mt-4 flex items-center gap-1.5 text-xs font-semibold text-onSurface-default-primary">
            {action}
            <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
          </span>
        )}
      </div>
      {!featured && <span className="sr-only">{action}</span>}
    </Link>
  );
}
