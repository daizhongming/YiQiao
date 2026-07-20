// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { useMemo, useState } from "react";
import { format } from "date-fns";
import { Save } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { DataTable } from "@/components/shared/data-table";
import { TableSkeleton } from "@/components/shared/table-skeleton";
import { EmptyState } from "@/components/self-hosted/empty-state";
import { toast } from "@/components/ui/use-toast";
import { getErrorMessage } from "@/lib/error-message";
import { api, getActiveProjectId } from "@/utils/api";
import { MEMORY_ENDPOINTS, SETTINGS_ENDPOINTS } from "@/utils/api-endpoints";
import { useApiQuery } from "@/hooks/use-api-query";
import { Memory } from "@/types/api";

const MEMORY_FETCH_LIMIT = 1000;
const ALL_CATEGORIES = "All";
const UNCATEGORIZED = "Uncategorized";

type WorkspaceSettings = {
  active_project_id?: string;
  categories?: { name: string; description?: string }[];
  projects?: {
    id: string;
    categories?: { name: string; description?: string }[];
  }[];
};

function normalizeCategories(value: unknown) {
  const rawItems = Array.isArray(value) ? value : value ? [value] : [];
  return rawItems
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean);
}

function uniqueCategories(categories: string[]) {
  const seen = new Set<string>();
  return categories.filter((category) => {
    const key = category.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function getMemoryCategories(memory: Memory) {
  const value = memory.categories?.length
    ? memory.categories
    : (memory.metadata?.categories ?? memory.metadata?.category);
  return uniqueCategories(normalizeCategories(value));
}

function getConfiguredCategories(settings: WorkspaceSettings | undefined) {
  if (!settings) return [];
  const activeProjectId =
    getActiveProjectId() || settings.active_project_id || "default-project";
  const project = settings.projects?.find(
    (item) => item.id === activeProjectId,
  );
  const rawCategories = project?.categories ?? settings.categories ?? [];
  return uniqueCategories(
    rawCategories
      .map((category) => category.name?.trim())
      .filter((category): category is string => Boolean(category)),
  );
}

function parseCategoryInput(value: string) {
  return uniqueCategories(
    value
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean),
  );
}

export default function CategoriesPage() {
  const [filter, setFilter] = useState(ALL_CATEGORIES);
  const [selectedMemory, setSelectedMemory] = useState<Memory | null>(null);
  const [categoryName, setCategoryName] = useState("");
  const [saving, setSaving] = useState(false);

  const {
    data: memories = [],
    isLoading,
    refetch,
  } = useApiQuery<Memory[]>(
    async () => {
      const res = await api.get(MEMORY_ENDPOINTS.BASE, {
        params: { top_k: MEMORY_FETCH_LIMIT },
      });
      const raw = res.data?.results ?? res.data ?? [];
      return Array.isArray(raw) ? raw : [];
    },
    { errorToast: "Failed to load memories", initialData: [] },
  );

  const { data: workspaceSettings, isLoading: isLoadingSettings } =
    useApiQuery<WorkspaceSettings>(
      async () => {
        const res = await api.get<WorkspaceSettings>(
          SETTINGS_ENDPOINTS.WORKSPACE,
        );
        return res.data;
      },
      { errorToast: "Failed to load workspace settings", initialData: {} },
    );

  const configuredCategories = useMemo(
    () => getConfiguredCategories(workspaceSettings),
    [workspaceSettings],
  );
  const memoryCategories = useMemo(
    () =>
      uniqueCategories(
        memories.flatMap((memory) => {
          const categories = getMemoryCategories(memory);
          return categories.length ? categories : [UNCATEGORIZED];
        }),
      ),
    [memories],
  );
  const categories = useMemo(
    () => uniqueCategories([...configuredCategories, ...memoryCategories]),
    [configuredCategories, memoryCategories],
  );
  const categoryCounts = useMemo(
    () =>
      categories.reduce<Record<string, number>>((counts, category) => {
        counts[category] = memories.filter((memory) => {
          const memoryCategoryList = getMemoryCategories(memory);
          if (category === UNCATEGORIZED)
            return memoryCategoryList.length === 0;
          return memoryCategoryList.includes(category);
        }).length;
        return counts;
      }, {}),
    [categories, memories],
  );
  const filteredMemories = memories.filter((memory) => {
    if (filter === ALL_CATEGORIES) return true;
    const memoryCategoryList = getMemoryCategories(memory);
    if (filter === UNCATEGORIZED) return memoryCategoryList.length === 0;
    return memoryCategoryList.includes(filter);
  });

  const isPageLoading = isLoading || isLoadingSettings;
  const categoryInputCategories = parseCategoryInput(categoryName);
  const canSaveCategory =
    Boolean(selectedMemory) && categoryInputCategories.length > 0 && !saving;

  const categoryInputPlaceholder = configuredCategories.length
    ? configuredCategories[0]
    : "e.g. Preferences";

  const saveCategory = async () => {
    if (!selectedMemory || !categoryInputCategories.length) return;
    setSaving(true);
    try {
      await api.put(MEMORY_ENDPOINTS.BY_ID(selectedMemory.id), {
        categories: categoryInputCategories,
      });
      toast({ title: "Category saved", variant: "success" });
      setSelectedMemory(null);
      setCategoryName("");
      await refetch();
    } catch (error) {
      toast({
        title: "Failed to save category",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const renderCategoryBadges = (memory: Memory) => {
    const memoryCategoryList = getMemoryCategories(memory);
    const displayCategories = memoryCategoryList.length
      ? memoryCategoryList
      : [UNCATEGORIZED];
    return (
      <div className="flex flex-wrap gap-1">
        {displayCategories.map((category) => (
          <Badge key={category} variant="outline">
            {category}
          </Badge>
        ))}
      </div>
    );
  };

  const selectMemory = (row: Memory) => {
    setSelectedMemory(row);
    setCategoryName(getMemoryCategories(row).join(", "));
  };

  const suggestedCategoryOptions = configuredCategories.filter(
    (category) => !parseCategoryInput(categoryName).includes(category),
  );

  const columns = [
    {
      key: "memory" as keyof Memory,
      label: "Memory",
      width: 360,
      render: (value: string) => <span className="line-clamp-2">{value}</span>,
    },
    {
      key: "categories" as keyof Memory,
      label: "Category",
      width: 140,
      render: (_value: Memory["categories"], row: Memory) =>
        renderCategoryBadges(row),
    },
    { key: "user_id" as keyof Memory, label: "User", width: 120 },
    {
      key: "updated_at" as keyof Memory,
      label: "Updated",
      width: 120,
      render: (value: string) =>
        value ? format(new Date(value), "MMM d, yyyy") : "--",
    },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold font-fustat">Custom Categories</h1>
        <p className="text-sm text-onSurface-default-secondary mt-1">
          Organize memories with project-level categories.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        {categories.slice(0, 4).map((category) => (
          <Card key={category} className="border-memBorder-primary">
            <CardContent className="p-5">
              <p className="truncate text-sm font-medium">{category}</p>
              <p className="mt-1 text-2xl font-semibold">
                {categoryCounts[category] ?? 0}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="flex flex-col gap-4 lg:flex-row">
        <Card className="border-memBorder-primary p-4 lg:w-72">
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="category-filter">Category</Label>
              <select
                id="category-filter"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                className="h-9 w-full rounded-md border border-memBorder-primary bg-surface-default-primary px-3 text-sm"
              >
                <option value={ALL_CATEGORIES}>All categories</option>
                {categories.map((category) => (
                  <option key={category} value={category}>
                    {category} ({categoryCounts[category] ?? 0})
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="category-name">Selected memory category</Label>
              <Input
                id="category-name"
                value={categoryName}
                onChange={(event) => setCategoryName(event.target.value)}
                disabled={!selectedMemory}
                placeholder={categoryInputPlaceholder}
              />
              {selectedMemory && suggestedCategoryOptions.length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {suggestedCategoryOptions.slice(0, 6).map((category) => (
                    <button
                      key={category}
                      type="button"
                      className="rounded-md border border-memBorder-primary px-2 py-1 text-xs text-onSurface-default-secondary hover:bg-surface-default-secondary"
                      onClick={() => {
                        const nextCategories = uniqueCategories([
                          ...parseCategoryInput(categoryName),
                          category,
                        ]);
                        setCategoryName(nextCategories.join(", "));
                      }}
                    >
                      {category}
                    </button>
                  ))}
                </div>
              ) : null}
              <Button
                className="w-full"
                onClick={saveCategory}
                disabled={!canSaveCategory}
              >
                <Save className="mr-2 size-4" />
                {saving ? "Saving..." : "Save category"}
              </Button>
            </div>
          </div>
        </Card>

        <Card className="min-w-0 flex-1 border-memBorder-primary overflow-hidden">
          {isPageLoading ? (
            <TableSkeleton rows={6} columns={4} />
          ) : memories.length === 0 ? (
            <EmptyState
              title="No memories yet"
              description="Categories appear once memories are stored."
            />
          ) : (
            <DataTable
              data={filteredMemories}
              columns={columns}
              getRowKey={(row) => row.id}
              onRowClick={selectMemory}
              getRowClassName={(row) =>
                selectedMemory?.id === row.id
                  ? "bg-surface-default-tertiary"
                  : undefined
              }
            />
          )}
        </Card>
      </div>
    </div>
  );
}
