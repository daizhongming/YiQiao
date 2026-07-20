"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { formatDistanceToNowStrict } from "date-fns";
import { enUS, zhCN } from "date-fns/locale";
import {
  ChevronRight,
  CircleUserRound,
  Clock3,
  Database,
  RefreshCw,
  Search,
  Tag,
  Trash2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { DataTable } from "@/components/shared/data-table";
import { TableSkeleton } from "@/components/shared/table-skeleton";
import { EmptyState } from "@/components/self-hosted/empty-state";
import {
  RequestDetailSheet,
  RequestLogTable,
} from "@/components/requests/request-activity";
import { toast } from "@/components/ui/use-toast";
import { getErrorMessage } from "@/lib/error-message";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { useApiQuery } from "@/hooks/use-api-query";
import { api } from "@/utils/api";
import {
  ENTITY_ENDPOINTS,
  MEMORY_ENDPOINTS,
  REQUEST_ENDPOINTS,
} from "@/utils/api-endpoints";
import {
  ApiRequestLog,
  EntityDetail,
  EntityType,
  Memory,
  RequestLogPage,
} from "@/types/api";

const REQUEST_PAGE_SIZE = 20;
const ENTITY_TYPES = new Set<EntityType>(["user", "agent", "app", "run"]);

const decodeEntityId = (value: string) => {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
};

export default function EntityDetailPage() {
  const { language } = useI18n();
  const dateLocale = language === "zh" ? zhCN : enUS;
  const numberLocale = language === "zh" ? "zh-CN" : "en-US";
  const params = useParams<{ type: string; id: string }>();
  const entityType = String(params.type ?? "") as EntityType;
  const entityId = decodeEntityId(String(params.id ?? ""));
  const validEntity = ENTITY_TYPES.has(entityType) && Boolean(entityId);
  const [activeTab, setActiveTab] = useState<"memories" | "requests">(
    "memories",
  );
  const [category, setCategory] = useState("overview");
  const [memorySearch, setMemorySearch] = useState("");
  const [requestPage, setRequestPage] = useState(1);
  const [selectedRequest, setSelectedRequest] = useState<ApiRequestLog | null>(
    null,
  );
  const requestQueryMounted = useRef(false);

  const {
    data: entity,
    isLoading: entityLoading,
    error: entityError,
    refetch: refetchEntity,
  } = useApiQuery<EntityDetail>(
    async () =>
      (
        await api.get<EntityDetail>(
          ENTITY_ENDPOINTS.DETAIL(entityType, entityId),
        )
      ).data,
    { enabled: validEntity, errorToast: "Failed to load entity" },
  );

  const {
    data: memories = [],
    isLoading: memoriesLoading,
    refetch: refetchMemories,
  } = useApiQuery<Memory[]>(
    async () => {
      const response = await api.get(MEMORY_ENDPOINTS.BASE, {
        params: { [`${entityType}_id`]: entityId, top_k: 1000 },
      });
      const raw = response.data?.results ?? response.data ?? [];
      return Array.isArray(raw) ? raw : [];
    },
    {
      enabled: validEntity,
      errorToast: "Failed to load entity memories",
      initialData: [],
    },
  );

  const {
    data: requestData = {
      items: [],
      total: 0,
      page: 1,
      page_size: REQUEST_PAGE_SIZE,
      series: [],
    },
    isLoading: requestsLoading,
    refetch: refetchRequests,
  } = useApiQuery<RequestLogPage>(
    async () =>
      (
        await api.get<RequestLogPage>(REQUEST_ENDPOINTS.BASE, {
          params: {
            page: requestPage,
            page_size: REQUEST_PAGE_SIZE,
            entity_type: entityType,
            entity_id: entityId,
          },
        })
      ).data,
    {
      enabled: validEntity,
      errorToast: "Failed to load entity requests",
      initialData: {
        items: [],
        total: 0,
        page: 1,
        page_size: REQUEST_PAGE_SIZE,
        series: [],
      },
    },
  );

  useEffect(() => {
    if (requestQueryMounted.current) void refetchRequests();
    else requestQueryMounted.current = true;
  }, [refetchRequests, requestPage]);

  const categories = useMemo(
    () =>
      Array.from(
        new Set(memories.flatMap((memory) => memory.categories ?? [])),
      ).sort(),
    [memories],
  );
  const filteredMemories = useMemo(() => {
    const term = memorySearch.trim().toLowerCase();
    return memories.filter((memory) => {
      if (category !== "overview" && !memory.categories?.includes(category)) {
        return false;
      }
      return !term || memory.memory.toLowerCase().includes(term);
    });
  }, [category, memories, memorySearch]);

  const deleteMemory = async (memory: Memory) => {
    try {
      await api.delete(MEMORY_ENDPOINTS.BY_ID(memory.id));
      toast({ title: "Memory deleted", variant: "success" });
      await Promise.all([refetchMemories(), refetchEntity()]);
    } catch (error) {
      toast({
        title: "Failed to delete memory",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const memoryColumns = [
    {
      key: "created_at" as keyof Memory,
      label: "Time",
      icon: Clock3,
      width: 150,
      render: (value: Memory[keyof Memory]) =>
        value
          ? formatDistanceToNowStrict(new Date(String(value)), {
              addSuffix: true,
              locale: dateLocale,
            })
          : "-",
    },
    {
      key: `${entityType}_id` as keyof Memory,
      label: "Entities",
      icon: CircleUserRound,
      width: 250,
      render: () => (
        <span className="inline-flex max-w-full items-center gap-1 rounded-sm bg-surface-default-secondary px-1.5 py-0.5">
          <CircleUserRound className="size-3.5 shrink-0" />
          <span className="truncate font-dm-mono text-xs">{entityId}</span>
        </span>
      ),
    },
    {
      key: "memory" as keyof Memory,
      label: "Memory Content",
      icon: Database,
      width: 360,
      render: (value: Memory[keyof Memory]) => (
        <span className="block truncate text-sm">{String(value)}</span>
      ),
    },
    {
      key: "categories" as keyof Memory,
      label: "Categories",
      icon: Tag,
      width: 240,
      render: (value: Memory[keyof Memory]) => {
        const values = Array.isArray(value) ? value : [];
        return values.length ? (
          <div className="flex min-w-0 gap-1">
            <Badge
              variant="outline"
              className="max-w-[150px] truncate rounded-sm font-dm-mono text-[11px]"
            >
              {values[0]}
            </Badge>
            {values.length > 1 && (
              <Badge
                variant="outline"
                className="rounded-sm font-dm-mono text-[11px]"
              >
                +{values.length - 1}
              </Badge>
            )}
          </div>
        ) : (
          "-"
        );
      },
    },
    {
      key: "id" as keyof Memory,
      label: "Action",
      width: 80,
      align: "center" as const,
      render: (_value: Memory[keyof Memory], row: Memory) => (
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          title="Delete memory"
          aria-label="Delete memory"
          onClick={() => void deleteMemory(row)}
        >
          <Trash2 className="size-3.5" />
        </Button>
      ),
    },
  ];

  if (!validEntity) {
    return (
      <EmptyState
        title="Invalid entity"
        description="The entity type or identifier in this URL is invalid."
      />
    );
  }

  if (entityError && !entityLoading) {
    return (
      <EmptyState
        title="Entity not found"
        description="This entity does not exist in the active project."
      />
    );
  }

  return (
    <div className="min-w-0 space-y-4">
      <div className="flex min-w-0 items-center gap-2 text-sm">
        <Link
          href="/dashboard/entities"
          className="text-onSurface-default-tertiary hover:text-onSurface-default-primary"
        >
          Entities
        </Link>
        <ChevronRight className="size-4 shrink-0 text-onSurface-default-tertiary" />
        <span className="truncate font-dm-mono text-xs">{entityId}</span>
      </div>

      {entityLoading || !entity ? (
        <TableSkeleton rows={3} columns={2} />
      ) : (
        <section className="overflow-hidden rounded-md border border-memBorder-primary bg-surface-default-primary">
          <div className="flex min-w-0 items-center gap-2 border-b border-memBorder-primary px-3 py-3">
            <CircleUserRound className="size-4 shrink-0" />
            <span className="truncate font-dm-mono text-sm">{entity.id}</span>
          </div>
          <div className="grid grid-cols-2 divide-x divide-memBorder-primary">
            <div className="p-3">
              <p className="flex items-center gap-2 text-xs text-onSurface-default-secondary">
                <Database className="size-4 text-blue-600" /> Total Memories
              </p>
              <p className="mt-1 text-lg font-medium">
                {entity.total_memories.toLocaleString(numberLocale)}
              </p>
            </div>
            <div className="p-3">
              <p className="flex items-center gap-2 text-xs text-onSurface-default-secondary">
                <CircleUserRound className="size-4 text-blue-600" /> Total
                Requests
              </p>
              <p className="mt-1 text-lg font-medium">
                {entity.total_requests.toLocaleString(numberLocale)}
              </p>
            </div>
          </div>
        </section>
      )}

      <div className="grid h-11 max-w-[424px] grid-cols-2 rounded-md bg-surface-default-tertiary p-1">
        {(
          [
            { value: "memories", label: "Memories" },
            { value: "requests", label: "Requests" },
          ] as const
        ).map((tab) => (
          <Button
            key={tab.value}
            variant="ghost"
            className={cn(
              "h-9 font-normal",
              activeTab === tab.value &&
                "border border-memBorder-primary bg-surface-default-primary shadow-sm",
            )}
            onClick={() => setActiveTab(tab.value)}
          >
            {tab.label}
          </Button>
        ))}
      </div>

      {activeTab === "memories" ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex max-w-full gap-2 overflow-x-auto pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
              {["overview", ...categories].map((item) => (
                <Button
                  key={item}
                  variant="outline"
                  className={cn(
                    "h-9 shrink-0 font-normal capitalize",
                    category === item &&
                      "border-onSurface-default-primary bg-surface-default-secondary",
                  )}
                  onClick={() => setCategory(item)}
                >
                  {item === "overview" && language === "zh"
                    ? "概览"
                    : item.replaceAll("_", " ")}
                </Button>
              ))}
            </div>
            <div className="flex min-w-0 gap-2">
              <div className="relative min-w-0">
                <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-onSurface-default-tertiary" />
                <Input
                  value={memorySearch}
                  placeholder="Search memories"
                  className="w-[220px] max-w-full pl-9"
                  onChange={(event) => setMemorySearch(event.target.value)}
                />
              </div>
              <Button
                variant="outline"
                size="icon"
                title="Refresh memories"
                aria-label="Refresh memories"
                onClick={() => void refetchMemories()}
              >
                <RefreshCw className="size-4" />
              </Button>
            </div>
          </div>

          {memoriesLoading ? (
            <TableSkeleton rows={9} columns={5} />
          ) : filteredMemories.length === 0 ? (
            <EmptyState
              title="No memories found"
              description="Memories stored for this entity will appear here."
            />
          ) : (
            <Card className="overflow-hidden border-memBorder-primary">
              <div className="divide-y divide-memBorder-primary md:hidden">
                {filteredMemories.map((memory) => (
                  <div key={memory.id} className="space-y-2 p-3">
                    <div className="flex items-center justify-between gap-2 text-xs text-onSurface-default-secondary">
                      <span>
                        {memory.created_at
                          ? formatDistanceToNowStrict(
                              new Date(memory.created_at),
                              { addSuffix: true, locale: dateLocale },
                            )
                          : "-"}
                      </span>
                      {memory.categories?.[0] && (
                        <Badge
                          variant="outline"
                          className="max-w-[140px] truncate rounded-sm font-dm-mono text-[11px]"
                        >
                          {memory.categories[0]}
                        </Badge>
                      )}
                    </div>
                    <p className="line-clamp-2 text-sm leading-5">
                      {memory.memory}
                    </p>
                    <div className="flex items-center justify-between gap-2">
                      <span className="inline-flex min-w-0 items-center gap-1 rounded-sm bg-surface-default-secondary px-1.5 py-0.5">
                        <CircleUserRound className="size-3.5 shrink-0" />
                        <span className="truncate font-dm-mono text-xs">
                          {entityId}
                        </span>
                      </span>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7 shrink-0"
                        title="Delete memory"
                        aria-label="Delete memory"
                        onClick={() => void deleteMemory(memory)}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
              <div className="hidden md:block">
                <DataTable
                  data={filteredMemories}
                  columns={memoryColumns}
                  getRowKey={(row) => row.id}
                />
              </div>
            </Card>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex justify-end">
            <Button
              variant="outline"
              className="h-9 gap-2"
              onClick={() => void refetchRequests()}
            >
              <RefreshCw className="size-4" /> Refresh
            </Button>
          </div>
          {requestsLoading ? (
            <TableSkeleton rows={9} columns={6} />
          ) : requestData.items.length === 0 ? (
            <EmptyState
              title="No requests found"
              description="Requests associated with this entity will appear here."
              image="requests"
            />
          ) : (
            <Card className="overflow-hidden border-memBorder-primary">
              <RequestLogTable
                logs={requestData.items}
                selectedId={selectedRequest?.id}
                onSelect={setSelectedRequest}
              />
            </Card>
          )}
          <div className="flex items-center justify-between text-sm text-onSurface-default-tertiary">
            <span>
              {requestData.total}
              {language === "zh" ? " 个请求" : " requests"}
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={requestPage <= 1 || requestsLoading}
                onClick={() => setRequestPage((page) => page - 1)}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={
                  requestPage * REQUEST_PAGE_SIZE >= requestData.total ||
                  requestsLoading
                }
                onClick={() => setRequestPage((page) => page + 1)}
              >
                Next
              </Button>
            </div>
          </div>
        </div>
      )}

      <RequestDetailSheet
        log={selectedRequest}
        onOpenChange={(open) => {
          if (!open) setSelectedRequest(null);
        }}
      />
    </div>
  );
}
