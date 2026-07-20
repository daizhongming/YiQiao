"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  Database,
  Gauge,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApiQuery } from "@/hooks/use-api-query";
import { api } from "@/utils/api";
import { USAGE_ENDPOINTS } from "@/utils/api-endpoints";
import {
  QuotaPolicy,
  UsageScopeType,
  UsageSubjects,
  UsageSummary,
} from "@/types/api";

const number = new Intl.NumberFormat();

const METRIC_LABELS: Record<QuotaPolicy["metric"], string> = {
  api_requests: "API requests",
  memory_writes: "Memory writes",
  memory_searches: "Memory searches",
  stored_memories: "Stored memories",
};

const PERIOD_LABELS: Record<QuotaPolicy["period"], string> = {
  minute: "minute",
  day: "day",
  month: "month",
  total: "total",
};

export default function UsagePage() {
  const [days, setDays] = useState("30");
  const [scopeType, setScopeType] =
    useState<Extract<UsageScopeType, "project" | "organization">>("project");

  const {
    data: subjects,
    isLoading: subjectsLoading,
    refetch: refetchSubjects,
  } = useApiQuery<UsageSubjects>(
    async () => (await api.get<UsageSubjects>(USAGE_ENDPOINTS.SUBJECTS)).data,
    { errorToast: "Failed to load usage scopes" },
  );

  const scopeId =
    scopeType === "organization"
      ? subjects?.organization.id
      : subjects?.project.id;

  const {
    data: summary,
    isLoading: summaryLoading,
    refetch: refetchSummary,
  } = useApiQuery<UsageSummary>(
    async () =>
      (
        await api.get<UsageSummary>(USAGE_ENDPOINTS.SUMMARY, {
          params: {
            days: Number(days),
            scope_type: scopeType,
            scope_id: scopeId,
            project_id: subjects?.project.id,
          },
        })
      ).data,
    { enabled: Boolean(scopeId), errorToast: "Failed to load usage" },
  );

  useEffect(() => {
    if (scopeId) void refetchSummary();
  }, [days, scopeId, scopeType, refetchSummary]);

  useEffect(() => {
    const refresh = () => void refetchSubjects();
    window.addEventListener("yiqiao-projects-updated", refresh);
    return () => window.removeEventListener("yiqiao-projects-updated", refresh);
  }, [refetchSubjects]);

  const chartData = useMemo(
    () =>
      (summary?.series ?? []).map((item) => ({
        ...item,
        label: new Date(`${item.date}T00:00:00`).toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
        }),
      })),
    [summary?.series],
  );

  const metrics = [
    {
      label: "Stored memories",
      value: summary?.totals.stored_memories ?? 0,
      icon: Database,
      tone: "text-blue-700 bg-blue-50 dark:text-blue-300 dark:bg-blue-950/40",
    },
    {
      label: "Memory writes",
      value: summary?.totals.memory_writes ?? 0,
      icon: Activity,
      tone: "text-teal-700 bg-teal-50 dark:text-teal-300 dark:bg-teal-950/40",
    },
    {
      label: "Memory searches",
      value: summary?.totals.memory_searches ?? 0,
      icon: Search,
      tone: "text-amber-700 bg-amber-50 dark:text-amber-300 dark:bg-amber-950/40",
    },
    {
      label: "API requests",
      value: summary?.totals.api_requests ?? 0,
      icon: Gauge,
      tone: "text-zinc-700 bg-zinc-100 dark:text-zinc-200 dark:bg-zinc-800",
    },
  ];

  const loading = subjectsLoading || summaryLoading || !summary;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold font-fustat">Usage</h1>
          <p className="mt-1 text-sm text-onSurface-default-secondary">
            {subjects?.organization.name ?? "Organization"} /{" "}
            {subjects?.project.name ?? "Project"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={scopeType}
            onValueChange={(value) => setScopeType(value as typeof scopeType)}
          >
            <SelectTrigger className="h-9 w-[150px]" variant="dropdown">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="project">Current project</SelectItem>
              <SelectItem value="organization">Organization</SelectItem>
            </SelectContent>
          </Select>
          <Select value={days} onValueChange={setDays}>
            <SelectTrigger className="h-9 w-[112px]" variant="dropdown">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="7">7 days</SelectItem>
              <SelectItem value="30">30 days</SelectItem>
              <SelectItem value="90">90 days</SelectItem>
            </SelectContent>
          </Select>
          {summary?.can_manage && (
            <Button asChild size="sm" variant="outline">
              <Link
                href={`/dashboard/settings/usage-limits?scope_type=${scopeType}&scope_id=${encodeURIComponent(scopeId ?? "")}&project_id=${encodeURIComponent(subjects?.project.id ?? "")}`}
              >
                <SlidersHorizontal className="mr-1.5 size-4" />
                Manage limits
              </Link>
            </Button>
          )}
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {metrics.map((metric) => (
          <Card key={metric.label} className="border-memBorder-primary">
            <CardContent className="flex min-h-[112px] items-center justify-between p-5">
              <div>
                <p className="text-sm text-onSurface-default-secondary">
                  {metric.label}
                </p>
                {loading ? (
                  <Skeleton className="mt-3 h-7 w-20" />
                ) : (
                  <p className="mt-2 text-2xl font-semibold tabular-nums">
                    {number.format(metric.value)}
                  </p>
                )}
              </div>
              <div
                className={`flex size-9 items-center justify-center rounded-md ${metric.tone}`}
              >
                <metric.icon className="size-4" />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card className="border-memBorder-primary">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm">Request activity</CardTitle>
            {!loading && (
              <span className="text-xs text-onSurface-default-tertiary">
                {summary.period.start} to {summary.period.end}
              </span>
            )}
          </div>
        </CardHeader>
        <CardContent className="h-[300px] px-2 pb-4 sm:px-5">
          {loading ? (
            <Skeleton className="h-full w-full" />
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart
                data={chartData}
                margin={{ top: 12, right: 12, left: -18, bottom: 0 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  vertical={false}
                  opacity={0.25}
                />
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 11 }}
                  minTickGap={24}
                />
                <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Area
                  type="monotone"
                  dataKey="api_requests"
                  name="Requests"
                  stroke="#2563eb"
                  fill="#2563eb"
                  fillOpacity={0.08}
                  strokeWidth={2}
                />
                <Area
                  type="monotone"
                  dataKey="memory_writes"
                  name="Writes"
                  stroke="#0f766e"
                  fill="#0f766e"
                  fillOpacity={0.08}
                  strokeWidth={2}
                />
                <Area
                  type="monotone"
                  dataKey="memory_searches"
                  name="Searches"
                  stroke="#b45309"
                  fill="#b45309"
                  fillOpacity={0.08}
                  strokeWidth={2}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {summary?.effective_limits.length ? (
        <Card className="border-memBorder-primary">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Effective limits</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {summary.effective_limits.map((policy) => (
              <div
                key={`${policy.scope_type}-${policy.scope_id}-${policy.metric}-${policy.period}`}
                className="space-y-2"
              >
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <div className="flex items-center gap-2">
                    <span>{METRIC_LABELS[policy.metric]}</span>
                    <span className="rounded bg-surface-default-secondary px-1.5 py-0.5 text-[11px] uppercase text-onSurface-default-secondary">
                      {policy.mode}
                    </span>
                  </div>
                  <span className="tabular-nums text-onSurface-default-secondary">
                    {number.format(policy.used ?? 0)} /{" "}
                    {number.format(policy.limit_value)} per{" "}
                    {PERIOD_LABELS[policy.period]}
                  </span>
                </div>
                <Progress
                  value={policy.percent ?? 0}
                  indicatorColor={
                    (policy.percent ?? 0) >= 90 ? "bg-red-500" : "bg-primary"
                  }
                  className="h-2"
                />
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}

      <Card className="border-memBorder-primary">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Attribution</CardTitle>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="api_keys">
            <TabsList className="h-8 rounded-md">
              <TabsTrigger
                value="api_keys"
                className="h-6 rounded px-2.5 text-xs"
              >
                API keys
              </TabsTrigger>
              <TabsTrigger
                value="members"
                className="h-6 rounded px-2.5 text-xs"
              >
                Members
              </TabsTrigger>
            </TabsList>
            <TabsContent value="api_keys" className="mt-4">
              <BreakdownTable
                empty="No attributed API key requests in this period."
                rows={(summary?.breakdown.api_keys ?? []).map((row) => ({
                  id: row.id,
                  label: row.label,
                  value: row.requests,
                }))}
              />
            </TabsContent>
            <TabsContent value="members" className="mt-4">
              <BreakdownTable
                empty="No attributed member requests in this period."
                rows={(summary?.breakdown.members ?? []).map((row) => ({
                  id: row.id,
                  label: row.email,
                  value: row.requests,
                }))}
              />
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {summary && !summary.metering.model_tokens_available && (
        <p className="text-xs text-onSurface-default-tertiary">
          Model tokens: unavailable from the configured provider.
        </p>
      )}
    </div>
  );
}

function BreakdownTable({
  rows,
  empty,
}: {
  rows: { id: string; label: string; value: number }[];
  empty: string;
}) {
  if (!rows.length)
    return (
      <p className="py-5 text-sm text-onSurface-default-secondary">{empty}</p>
    );
  return (
    <div className="divide-y divide-memBorder-primary">
      {rows.map((row) => (
        <div
          key={row.id}
          className="flex min-h-10 items-center justify-between gap-4 py-2 text-sm"
        >
          <span className="min-w-0 truncate">{row.label}</span>
          <span className="shrink-0 tabular-nums text-onSurface-default-secondary">
            {number.format(row.value)} requests
          </span>
        </div>
      ))}
    </div>
  );
}
