"use client";

import { useEffect, useMemo, useState } from "react";
import { format, formatDistanceToNowStrict } from "date-fns";
import { enUS, zhCN } from "date-fns/locale";
import {
  Boxes,
  CheckCircle2,
  CircleX,
  Clock3,
  Copy,
  ListChecks,
  Timer,
  UserRound,
  Workflow,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataTable } from "@/components/shared/data-table";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { ApiRequestLog } from "@/types/api";

const EVENT_STYLES: Record<string, string> = {
  ADD: "border-emerald-100 bg-emerald-50 text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/40 dark:text-emerald-300",
  SEARCH:
    "border-indigo-100 bg-indigo-50 text-indigo-700 dark:border-indigo-900/40 dark:bg-indigo-950/40 dark:text-indigo-300",
  GET_ALL:
    "border-sky-100 bg-sky-50 text-sky-700 dark:border-sky-900/40 dark:bg-sky-950/40 dark:text-sky-300",
};

export const formatLatency = (latencyMs: number, language: "en" | "zh") =>
  latencyMs >= 1000
    ? `${(latencyMs / 1000).toFixed(2)} ${language === "zh" ? "秒" : "s"}`
    : `${latencyMs.toFixed(2)} ms`;

export function RequestEventBadge({ event }: { event: string }) {
  const { language } = useI18n();
  const eventLabels: Record<string, string> = {
    ADD: "新增",
    SEARCH: "搜索",
    GET_ALL: "获取全部",
  };
  return (
    <Badge
      variant="outline"
      className={cn(
        "rounded-sm px-1.5 py-0 font-dm-mono text-[11px] font-medium",
        EVENT_STYLES[event] ??
          "border-memBorder-primary bg-surface-default-secondary",
      )}
    >
      {language === "zh"
        ? (eventLabels[event] ?? event.replace("_", " "))
        : event.replace("_", " ")}
    </Badge>
  );
}

function EntityList({ log }: { log: ApiRequestLog }) {
  if (!log.entities.length) {
    return <span className="text-onSurface-default-tertiary">-</span>;
  }
  const first = log.entities[0];
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <span className="inline-flex min-w-0 items-center gap-1 rounded-sm bg-surface-default-secondary px-1.5 py-0.5">
        <UserRound className="size-3.5 shrink-0" />
        <span className="truncate font-dm-mono text-xs">{first.id}</span>
      </span>
      {log.entities.length > 1 && (
        <span className="shrink-0 rounded-sm bg-surface-default-tertiary px-1.5 py-0.5 font-dm-mono text-xs">
          + {log.entities.length - 1}
        </span>
      )}
    </div>
  );
}

interface RequestLogTableProps {
  logs: ApiRequestLog[];
  selectedId?: string;
  onSelect: (log: ApiRequestLog) => void;
}

export function RequestLogTable({
  logs,
  selectedId,
  onSelect,
}: RequestLogTableProps) {
  const { language } = useI18n();
  const dateLocale = language === "zh" ? zhCN : enUS;
  const columns = useMemo(
    () => [
      {
        key: "created_at" as keyof ApiRequestLog,
        label: "Time",
        icon: Clock3,
        width: 135,
        render: (value: ApiRequestLog[keyof ApiRequestLog]) => (
          <span
            title={format(new Date(String(value)), "PPpp", {
              locale: dateLocale,
            })}
          >
            {formatDistanceToNowStrict(new Date(String(value)), {
              addSuffix: true,
              locale: dateLocale,
            })}
          </span>
        ),
      },
      {
        key: "event_type" as keyof ApiRequestLog,
        label: "Type",
        icon: Workflow,
        width: 145,
        render: (value: ApiRequestLog[keyof ApiRequestLog]) => (
          <RequestEventBadge event={String(value)} />
        ),
      },
      {
        key: "entities" as keyof ApiRequestLog,
        label: "Entities",
        icon: Boxes,
        width: 300,
        render: (
          _value: ApiRequestLog[keyof ApiRequestLog],
          row: ApiRequestLog,
        ) => <EntityList log={row} />,
      },
      {
        key: "result_count" as keyof ApiRequestLog,
        label: "Event",
        icon: ListChecks,
        width: 200,
        render: (
          _value: ApiRequestLog[keyof ApiRequestLog],
          row: ApiRequestLog,
        ) =>
          row.result_count ? (
            <span className="rounded-sm bg-surface-default-tertiary px-2 py-0.5 font-dm-mono text-xs">
              + {row.result_count}
            </span>
          ) : (
            <span className="text-onSurface-default-tertiary">-</span>
          ),
      },
      {
        key: "latency_ms" as keyof ApiRequestLog,
        label: "Latency",
        icon: Timer,
        width: 145,
        render: (value: ApiRequestLog[keyof ApiRequestLog]) =>
          formatLatency(Number(value), language),
      },
      {
        key: "status" as keyof ApiRequestLog,
        label: "Status",
        icon: CheckCircle2,
        width: 100,
        align: "center" as const,
        render: (
          _value: ApiRequestLog[keyof ApiRequestLog],
          row: ApiRequestLog,
        ) =>
          row.status === "succeeded" ? (
            <CheckCircle2
              className="mx-auto size-4 text-emerald-600"
              aria-label="Succeeded"
            />
          ) : (
            <CircleX
              className="mx-auto size-4 text-rose-600"
              aria-label="Failed"
            />
          ),
      },
    ],
    [dateLocale, language],
  );

  return (
    <>
      <div className="divide-y divide-memBorder-primary md:hidden">
        {logs.map((log) => (
          <button
            key={log.id}
            type="button"
            className={cn(
              "block w-full space-y-2 px-3 py-3 text-left hover:bg-surface-default-secondary",
              selectedId === log.id && "bg-surface-default-tertiary",
            )}
            onClick={() => onSelect(log)}
          >
            <div className="flex items-center justify-between gap-3">
              <span className="min-w-0 truncate text-xs text-onSurface-default-secondary">
                {formatDistanceToNowStrict(new Date(log.created_at), {
                  addSuffix: true,
                  locale: dateLocale,
                })}
              </span>
              <RequestEventBadge event={log.event_type} />
            </div>
            <div className="flex min-w-0 items-center justify-between gap-3">
              <div className="min-w-0 flex-1">
                <EntityList log={log} />
              </div>
              <span className="shrink-0 font-dm-mono text-xs">
                {formatLatency(log.latency_ms, language)}
              </span>
              {log.status === "succeeded" ? (
                <CheckCircle2 className="size-4 shrink-0 text-emerald-600" />
              ) : (
                <CircleX className="size-4 shrink-0 text-rose-600" />
              )}
            </div>
          </button>
        ))}
      </div>
      <div className="hidden md:block">
        <DataTable
          data={logs}
          columns={columns}
          getRowKey={(row) => row.id}
          onRowClick={onSelect}
          getRowClassName={(row) =>
            selectedId === row.id ? "bg-surface-default-tertiary" : undefined
          }
        />
      </div>
    </>
  );
}

const prettyJson = (value: unknown) => JSON.stringify(value ?? {}, null, 2);

function CopyButton({ value, label }: { value: unknown; label: string }) {
  return (
    <Button
      variant="ghost"
      size="icon"
      className="size-7"
      title={`Copy ${label}`}
      aria-label={`Copy ${label}`}
      onClick={() =>
        void navigator.clipboard.writeText(
          typeof value === "string" ? value : prettyJson(value),
        )
      }
    >
      <Copy className="size-3.5" />
    </Button>
  );
}

interface RequestDetailSheetProps {
  log: ApiRequestLog | null;
  onOpenChange: (open: boolean) => void;
}

export function RequestDetailSheet({
  log,
  onOpenChange,
}: RequestDetailSheetProps) {
  const { language, t } = useI18n();
  const dateLocale = language === "zh" ? zhCN : enUS;
  const [activeTab, setActiveTab] = useState<"payload" | "results">("payload");
  useEffect(() => setActiveTab("payload"), [log?.id]);
  const query =
    log?.request_payload && typeof log.request_payload === "object"
      ? (log.request_payload as Record<string, unknown>).query
      : null;

  return (
    <Sheet open={!!log} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto p-0 sm:max-w-[720px]">
        <SheetHeader className="h-12 justify-center border-b border-memBorder-primary px-3 pr-12">
          <SheetTitle className="flex items-center gap-2 text-base font-medium">
            Event {log && <RequestEventBadge event={log.event_type} />}
          </SheetTitle>
          <SheetDescription className="sr-only">
            Request payload and retrieved memories
          </SheetDescription>
        </SheetHeader>

        {log && (
          <div className="pb-8">
            <div className="grid h-10 grid-cols-2 border-b border-memBorder-primary text-xs font-medium">
              <button
                type="button"
                className={cn(
                  "flex items-center justify-center",
                  activeTab === "payload"
                    ? "border-b-2 border-onSurface-default-primary"
                    : "text-onSurface-default-secondary",
                )}
                onClick={() => setActiveTab("payload")}
              >
                Request Payload
              </button>
              <button
                type="button"
                className={cn(
                  "flex items-center justify-center",
                  activeTab === "results"
                    ? "border-b-2 border-onSurface-default-primary"
                    : "text-onSurface-default-secondary",
                )}
                onClick={() => setActiveTab("results")}
              >
                {t("Retrieved Memories")} ({log.result_count ?? 0})
              </button>
            </div>

            <div className="space-y-3 p-2">
              {activeTab === "payload" ? (
                <>
                  {typeof query === "string" && query && (
                    <section className="overflow-hidden rounded-md border border-memBorder-primary">
                      <div className="border-b border-memBorder-primary bg-surface-default-secondary px-2 py-1.5 text-xs font-medium">
                        Search Query
                      </div>
                      <p className="px-2 py-2 text-sm">{query}</p>
                    </section>
                  )}

                  <section className="overflow-hidden rounded-md border border-memBorder-primary">
                    <div className="flex items-center justify-between border-b border-memBorder-primary px-2 py-1.5 text-xs">
                      <span className="font-medium">ID</span>
                      <span className="flex min-w-0 items-center gap-1 font-dm-mono">
                        <span className="truncate">{log.id}</span>
                        <CopyButton value={log.id} label="request ID" />
                      </span>
                    </div>
                    <dl className="grid grid-cols-3 divide-x divide-memBorder-primary text-xs">
                      <div className="p-2">
                        <dt className="text-onSurface-default-tertiary">
                          Latency
                        </dt>
                        <dd className="mt-1">
                          {formatLatency(log.latency_ms, language)}
                        </dd>
                      </div>
                      <div className="p-2">
                        <dt className="text-onSurface-default-tertiary">
                          Requested At
                        </dt>
                        <dd className="mt-1">
                          {format(new Date(log.created_at), "PPpp", {
                            locale: dateLocale,
                          })}
                        </dd>
                      </div>
                      <div className="p-2">
                        <dt className="text-onSurface-default-tertiary">
                          Status
                        </dt>
                        <dd className="mt-1 capitalize">
                          {language === "zh"
                            ? log.status === "succeeded"
                              ? "成功"
                              : "失败"
                            : log.status}
                        </dd>
                      </div>
                    </dl>
                  </section>

                  <section className="overflow-hidden rounded-md border border-memBorder-primary">
                    <div className="flex items-center justify-between border-b border-memBorder-primary bg-surface-default-secondary px-2 py-1.5 text-xs font-medium">
                      <span>Payload</span>
                      <CopyButton
                        value={log.request_payload}
                        label="request payload"
                      />
                    </div>
                    <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap break-words p-3 font-dm-mono text-xs leading-6">
                      {prettyJson(log.request_payload)}
                    </pre>
                  </section>
                </>
              ) : (
                <section className="overflow-hidden rounded-md border border-memBorder-primary">
                  <div className="flex items-center justify-between border-b border-memBorder-primary bg-surface-default-secondary px-2 py-1.5 text-xs font-medium">
                    <span>Retrieved Memories</span>
                    <CopyButton
                      value={log.response_payload}
                      label="retrieved memories"
                    />
                  </div>
                  <pre className="max-h-[640px] overflow-auto whitespace-pre-wrap break-words p-3 font-dm-mono text-xs leading-6">
                    {log.response_payload
                      ? prettyJson(log.response_payload)
                      : t("No response payload was recorded for this request.")}
                  </pre>
                </section>
              )}
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
