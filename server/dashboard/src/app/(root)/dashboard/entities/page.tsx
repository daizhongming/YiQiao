// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { format, formatDistanceToNowStrict } from "date-fns";
import { enUS, zhCN } from "date-fns/locale";
import { Clock3, RefreshCw, Search, Trash2, UserRound } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DataTable } from "@/components/shared/data-table";
import { TableSkeleton } from "@/components/shared/table-skeleton";
import { EmptyState } from "@/components/self-hosted/empty-state";
import DeleteConfirmationModal from "@/components/ui/delete-confirmation-modal";
import { toast } from "@/components/ui/use-toast";
import { api } from "@/utils/api";
import { ENTITY_ENDPOINTS } from "@/utils/api-endpoints";
import { getErrorMessage } from "@/lib/error-message";
import { useI18n, type Language } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { useApiQuery } from "@/hooks/use-api-query";
import { Entity, EntityType } from "@/types/api";

type RangeKey = "all" | "1d" | "7d" | "30d" | "90d";

const ENTITY_TYPES: EntityType[] = ["user", "run", "agent", "app"];
const RANGE_OPTIONS: { value: RangeKey; label: string; days?: number }[] = [
  { value: "all", label: "All time" },
  { value: "1d", label: "Last day", days: 1 },
  { value: "7d", label: "Last 7 days", days: 7 },
  { value: "30d", label: "Last 30 days", days: 30 },
  { value: "90d", label: "Last 90 days", days: 90 },
];

function entityTypeLabel(type: EntityType, language: Language) {
  if (language === "en") {
    return `${type.charAt(0).toUpperCase()}${type.slice(1)}`;
  }
  return { user: "用户", agent: "智能体", app: "应用", run: "运行" }[type];
}

export default function EntitiesPage() {
  const { language } = useI18n();
  const dateLocale = language === "zh" ? zhCN : enUS;
  const router = useRouter();
  const [activeType, setActiveType] = useState<EntityType>("user");
  const [range, setRange] = useState<RangeKey>("all");
  const [search, setSearch] = useState("");
  const [entityToDelete, setEntityToDelete] = useState<Entity | null>(null);

  const {
    data: entities = [],
    isLoading,
    refetch,
  } = useApiQuery<Entity[]>(
    async () => {
      const response = await api.get<Entity[]>(ENTITY_ENDPOINTS.BASE);
      return response.data ?? [];
    },
    { errorToast: "Failed to load entities", initialData: [] },
  );

  const filteredEntities = useMemo(() => {
    const selectedRange = RANGE_OPTIONS.find((item) => item.value === range);
    const cutoff = selectedRange?.days
      ? Date.now() - selectedRange.days * 24 * 60 * 60 * 1000
      : null;
    const term = search.trim().toLowerCase();
    return entities.filter((entity) => {
      if (entity.type !== activeType) return false;
      if (term && !entity.id.toLowerCase().includes(term)) return false;
      if (cutoff) {
        const timestamp = entity.updated_at ?? entity.created_at;
        if (!timestamp || new Date(timestamp).getTime() < cutoff) return false;
      }
      return true;
    });
  }, [activeType, entities, range, search]);

  const handleDelete = async () => {
    if (!entityToDelete) return;
    try {
      await api.delete(
        ENTITY_ENDPOINTS.BY_ID(entityToDelete.type, entityToDelete.id),
      );
      toast({ title: "Entity deleted", variant: "success" });
      setEntityToDelete(null);
      void refetch();
    } catch (error) {
      toast({
        title: "Failed to delete entity",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const columns = [
    {
      key: "id" as keyof Entity,
      label: `${entityTypeLabel(activeType, language)} ID`,
      icon: UserRound,
      width: 420,
      render: (value: Entity[keyof Entity]) => (
        <span className="block truncate font-dm-mono text-xs">
          {String(value)}
        </span>
      ),
    },
    {
      key: "total_memories" as keyof Entity,
      label: "Memories",
      width: 140,
      render: (value: Entity[keyof Entity]) => (
        <Badge variant="outline" className="rounded-sm font-dm-mono">
          {String(value)}
        </Badge>
      ),
    },
    {
      key: "created_at" as keyof Entity,
      label: "Created",
      icon: Clock3,
      width: 180,
      render: (value: Entity[keyof Entity]) =>
        value
          ? format(
              new Date(String(value)),
              language === "zh" ? "yyyy年M月d日" : "MMM d, yyyy",
              {
                locale: dateLocale,
              },
            )
          : "-",
    },
    {
      key: "updated_at" as keyof Entity,
      label: "Last Active",
      width: 180,
      render: (value: Entity[keyof Entity]) =>
        value
          ? formatDistanceToNowStrict(new Date(String(value)), {
              addSuffix: true,
              locale: dateLocale,
            })
          : "-",
    },
    {
      key: "id" as keyof Entity,
      label: "Action",
      width: 80,
      align: "center" as const,
      render: (_value: Entity[keyof Entity], row: Entity) => (
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          title="Delete entity"
          aria-label="Delete entity"
          onClick={(event) => {
            event.stopPropagation();
            setEntityToDelete(row);
          }}
        >
          <Trash2 className="size-3.5" />
        </Button>
      ),
    },
  ];

  return (
    <div className="min-w-0 space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-fustat text-xl font-semibold">Entities</h1>
        <Select
          value={range}
          onValueChange={(value) => setRange(value as RangeKey)}
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

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex max-w-full gap-2 overflow-x-auto pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {ENTITY_TYPES.map((type) => (
            <Button
              key={type}
              variant="outline"
              className={cn(
                "h-9 min-w-[88px] font-normal",
                activeType === type &&
                  "border-onSurface-default-primary bg-surface-default-secondary",
              )}
              onClick={() => setActiveType(type)}
            >
              {language === "zh"
                ? entityTypeLabel(type, language)
                : type.toUpperCase()}
            </Button>
          ))}
        </div>
        <div className="flex min-w-0 gap-2">
          <div className="relative min-w-0">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-onSurface-default-tertiary" />
            <Input
              value={search}
              placeholder="Search entity ID"
              className="w-[220px] max-w-full pl-9"
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <Button
            variant="outline"
            size="icon"
            title="Refresh entities"
            aria-label="Refresh entities"
            disabled={isLoading}
            onClick={() => void refetch()}
          >
            <RefreshCw className={cn("size-4", isLoading && "animate-spin")} />
          </Button>
        </div>
      </div>

      {isLoading ? (
        <TableSkeleton rows={8} columns={5} />
      ) : filteredEntities.length === 0 ? (
        <EmptyState
          title={`No ${activeType} entities found`}
          description="Entities appear when memories are stored with matching identifiers."
        />
      ) : (
        <Card className="overflow-hidden border-memBorder-primary">
          <div className="divide-y divide-memBorder-primary md:hidden">
            {filteredEntities.map((entity) => (
              <div
                key={`${entity.type}:${entity.id}`}
                className="flex items-center gap-2 p-3"
              >
                <button
                  type="button"
                  className="min-w-0 flex-1 space-y-2 text-left"
                  onClick={() =>
                    router.push(
                      `/dashboard/entities/${entity.type}/${encodeURIComponent(entity.id)}`,
                    )
                  }
                >
                  <span className="block truncate font-dm-mono text-sm">
                    {entity.id}
                  </span>
                  <span className="flex items-center gap-3 text-xs text-onSurface-default-secondary">
                    <span>
                      {entity.total_memories}
                      {language === "zh" ? " 条记忆" : " memories"}
                    </span>
                    <span>
                      {entity.updated_at
                        ? formatDistanceToNowStrict(
                            new Date(entity.updated_at),
                            { addSuffix: true, locale: dateLocale },
                          )
                        : language === "zh"
                          ? "暂无活动"
                          : "No activity"}
                    </span>
                  </span>
                </button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8 shrink-0"
                  title="Delete entity"
                  aria-label="Delete entity"
                  onClick={() => setEntityToDelete(entity)}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            ))}
          </div>
          <div className="hidden md:block">
            <DataTable
              data={filteredEntities}
              columns={columns}
              getRowKey={(row) => `${row.type}:${row.id}`}
              onRowClick={(row) =>
                router.push(
                  `/dashboard/entities/${row.type}/${encodeURIComponent(row.id)}`,
                )
              }
            />
          </div>
        </Card>
      )}

      <DeleteConfirmationModal
        isOpen={!!entityToDelete}
        onClose={() => setEntityToDelete(null)}
        onConfirm={handleDelete}
        title="Delete entity"
        description="All memories associated with this entity will be permanently removed. This cannot be undone."
        itemName={entityToDelete?.id ?? ""}
        confirmButtonText="Delete"
      />
    </div>
  );
}
