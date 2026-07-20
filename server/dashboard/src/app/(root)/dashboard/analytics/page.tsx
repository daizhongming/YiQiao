// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { useMemo } from "react";
import { format, startOfDay, subDays } from "date-fns";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataTable } from "@/components/shared/data-table";
import { TableSkeleton } from "@/components/shared/table-skeleton";
import { api } from "@/utils/api";
import {
  MEMORY_ENDPOINTS,
  REQUEST_ENDPOINTS,
  ENTITY_ENDPOINTS,
} from "@/utils/api-endpoints";
import { useApiQuery } from "@/hooks/use-api-query";
import { ApiRequestLog, Entity, Memory } from "@/types/api";

const MEMORY_FETCH_LIMIT = 1000;
const REQUEST_LOG_LIMIT = 200;
const ENTITY_TYPE_LABELS = {
  user: "Users",
  agent: "Agents",
  run: "Runs",
} as const;

type EndpointStat = {
  path: string;
  count: number;
  avgLatency: number;
  successRate: string;
};

export default function AnalyticsPage() {
  const { data: memories = [], isLoading: memoriesLoading } = useApiQuery<
    Memory[]
  >(
    async () => {
      const res = await api.get(MEMORY_ENDPOINTS.BASE, {
        params: { top_k: MEMORY_FETCH_LIMIT },
      });
      const raw = res.data?.results ?? res.data ?? [];
      return Array.isArray(raw) ? raw : [];
    },
    { errorToast: "Failed to load memories", initialData: [] },
  );

  const { data: logs = [], isLoading: logsLoading } = useApiQuery<
    ApiRequestLog[]
  >(
    async () => {
      const res = await api.get<ApiRequestLog[]>(REQUEST_ENDPOINTS.BASE, {
        params: { limit: REQUEST_LOG_LIMIT },
      });
      return res.data ?? [];
    },
    { errorToast: "Failed to load request logs", initialData: [] },
  );

  const { data: entities = [] } = useApiQuery<Entity[]>(
    async () => {
      const res = await api.get<Entity[]>(ENTITY_ENDPOINTS.BASE);
      return res.data ?? [];
    },
    { errorToast: "Failed to load entities", initialData: [] },
  );

  const isLoading = memoriesLoading || logsLoading;

  const successfulRequests = logs.filter((log) => log.status_code < 400).length;
  const avgLatency = logs.length
    ? Math.round(
        logs.reduce((sum, log) => sum + log.latency_ms, 0) / logs.length,
      )
    : 0;
  const successRate = logs.length
    ? `${Math.round((successfulRequests / logs.length) * 100)}%`
    : "--";

  const dailyCounts = useMemo(() => {
    const days = Array.from({ length: 7 }, (_, index) =>
      startOfDay(subDays(new Date(), 6 - index)),
    );
    return days.map((day) => {
      const nextDay = new Date(day);
      nextDay.setDate(day.getDate() + 1);
      return {
        label: format(day, "MMM d"),
        count: logs.filter((log) => {
          const created = new Date(log.created_at);
          return created >= day && created < nextDay;
        }).length,
      };
    });
  }, [logs]);

  const endpointStats = useMemo<EndpointStat[]>(() => {
    const buckets = new Map<string, ApiRequestLog[]>();
    for (const log of logs) {
      const key = `${log.method} ${log.path}`;
      buckets.set(key, [...(buckets.get(key) ?? []), log]);
    }
    return Array.from(buckets.entries())
      .map(([path, items]) => {
        const ok = items.filter((item) => item.status_code < 400).length;
        return {
          path,
          count: items.length,
          avgLatency: Math.round(
            items.reduce((sum, item) => sum + item.latency_ms, 0) /
              items.length,
          ),
          successRate: `${Math.round((ok / items.length) * 100)}%`,
        };
      })
      .sort((a, b) => b.count - a.count)
      .slice(0, 8);
  }, [logs]);

  const maxDailyCount = Math.max(1, ...dailyCounts.map((day) => day.count));

  const columns = [
    { key: "path" as keyof EndpointStat, label: "Endpoint", width: 320 },
    {
      key: "count" as keyof EndpointStat,
      label: "Requests",
      width: 100,
      align: "right" as const,
    },
    {
      key: "avgLatency" as keyof EndpointStat,
      label: "Avg Latency",
      width: 120,
      render: (value: number) => `${value} ms`,
      align: "right" as const,
    },
    {
      key: "successRate" as keyof EndpointStat,
      label: "Success",
      width: 100,
      align: "right" as const,
    },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold font-fustat">Analytics</h1>
        <p className="text-sm text-onSurface-default-secondary mt-1">
          Usage, latency, and memory growth from this YiQiao instance.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        {[
          { label: "Requests", value: logs.length || "--" },
          { label: "Success Rate", value: successRate },
          {
            label: "Avg Latency",
            value: logs.length ? `${avgLatency} ms` : "--",
          },
          {
            label: "Memories",
            value: memoriesLoading ? "--" : memories.length,
          },
        ].map((stat) => (
          <Card key={stat.label} className="border-memBorder-primary">
            <CardContent className="p-5">
              <p className="text-xs text-onSurface-default-tertiary">
                {stat.label}
              </p>
              <p className="mt-1 text-2xl font-semibold">{stat.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[2fr_1fr]">
        <Card className="border-memBorder-primary">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Requests over 7 days</CardTitle>
          </CardHeader>
          <CardContent className="p-4">
            {isLoading ? (
              <TableSkeleton rows={4} columns={1} />
            ) : (
              <div className="flex h-[220px] items-end gap-2">
                {dailyCounts.map((day) => (
                  <div
                    key={day.label}
                    className="flex flex-1 flex-col items-center gap-2"
                  >
                    <div
                      className="w-full rounded-t bg-surface-default-brand"
                      style={{
                        height: `${Math.max(8, (day.count / maxDailyCount) * 180)}px`,
                      }}
                      title={`${day.count} requests`}
                    />
                    <span className="text-[11px] text-onSurface-default-tertiary">
                      {day.label}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="border-memBorder-primary">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Entities</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 p-4">
            {(["user", "agent", "run"] as const).map((type) => (
              <div key={type} className="flex items-center justify-between">
                <span className="capitalize text-sm text-onSurface-default-secondary">
                  {ENTITY_TYPE_LABELS[type]}
                </span>
                <span className="text-sm font-semibold">
                  {entities.filter((entity) => entity.type === type).length}
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <Card className="border-memBorder-primary overflow-hidden">
        {isLoading ? (
          <TableSkeleton rows={5} columns={4} />
        ) : (
          <DataTable
            data={endpointStats}
            columns={columns}
            getRowKey={(row) => row.path}
          />
        )}
      </Card>
    </div>
  );
}
