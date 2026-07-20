"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import { Network, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/use-toast";
import { useApiQuery } from "@/hooks/use-api-query";
import { getErrorMessage } from "@/lib/error-message";
import { useI18n } from "@/lib/i18n";
import { api } from "@/utils/api";
import { GRAPH_ENDPOINTS } from "@/utils/api-endpoints";
import type { GraphEntity, GraphResponse } from "@/types/api";

const GalaxyGraph = dynamic(() => import("./galaxy-graph"), {
  ssr: false,
  loading: () => <GraphLoading />,
});

function pageCopy(language: "en" | "zh") {
  if (language === "zh") {
    return {
      title: "\u8bb0\u5fc6\u661f\u56fe",
      project: "Neo4j \u5173\u7cfb\u7f51\u7edc",
      sync: "\u540c\u6b65 Neo4j",
      syncing: "\u540c\u6b65\u4e2d...",
      loadError: "\u56fe\u8c31\u4e0d\u53ef\u7528",
      notEnabled: "Neo4j \u672a\u542f\u7528",
      notEnabledDescription:
        "\u8bbe\u7f6e NEO4J_ENABLED=true \u5e76\u5728 server/.env \u4e2d\u586b\u5199 Neo4j \u51ed\u636e\uff0c\u7136\u540e\u91cd\u542f API\u3002",
      unavailable: "Neo4j \u4e0d\u53ef\u7528",
      empty: "\u6682\u65e0\u56fe\u8c31\u6570\u636e",
      emptyDescription:
        "\u6dfb\u52a0\u5e26\u6709 user_id\u3001agent_id\u3001app_id \u6216 run_id \u7684\u8bb0\u5fc6\u540e\u540c\u6b65 Neo4j\u3002",
      entityError: "\u5b9e\u4f53\u5217\u8868\u52a0\u8f7d\u5931\u8d25",
    };
  }
  return {
    title: "Memory Galaxy",
    project: "Neo4j relationship network",
    sync: "Sync Neo4j",
    syncing: "Syncing...",
    loadError: "Graph is unavailable",
    notEnabled: "Neo4j is not enabled",
    notEnabledDescription:
      "Set NEO4J_ENABLED=true and fill Neo4j credentials in server/.env, then restart the API.",
    unavailable: "Neo4j is unavailable",
    empty: "No graph data",
    emptyDescription:
      "Add memories with user_id, agent_id, app_id, or run_id, then sync Neo4j.",
    entityError: "Graph entities could not be loaded",
  };
}

function GraphLoading() {
  return (
    <div
      className="relative h-[calc(100dvh-176px)] min-h-[620px] max-h-[920px] overflow-hidden rounded-md bg-[#05070a]"
      aria-busy="true"
    >
      <div className="absolute inset-0 animate-pulse opacity-70">
        {Array.from({ length: 46 }).map((_, index) => {
          const left = (index * 37) % 97;
          const top = (index * 61) % 91;
          const size = index % 9 === 0 ? 4 : 2;
          return (
            <span
              key={index}
              className="absolute rounded-full bg-white"
              style={{
                left: `${left}%`,
                top: `${top}%`,
                width: size,
                height: size,
              }}
            />
          );
        })}
      </div>
      <div className="absolute left-3 top-3 h-10 w-72 max-w-[70%] border border-white/10 bg-white/[0.04]" />
      <div className="absolute bottom-3 left-3 h-9 w-36 border border-white/10 bg-white/[0.04]" />
    </div>
  );
}

function GraphState({
  title,
  description,
  danger = false,
}: {
  title: string;
  description: string;
  danger?: boolean;
}) {
  return (
    <div className="flex min-h-[620px] items-center justify-center rounded-md border border-memBorder-primary bg-surface-default-secondary px-4 py-8">
      <div className="w-full max-w-md text-center">
        <span
          className={`mx-auto flex size-12 items-center justify-center rounded-full ${
            danger
              ? "bg-red-50 text-red-600 dark:bg-red-950/40 dark:text-red-300"
              : "bg-surface-default-tertiary text-onSurface-default-secondary"
          }`}
        >
          <Network className="size-5" />
        </span>
        <h2 className="mt-4 text-base font-semibold">{title}</h2>
        <p className="mx-auto mt-2 max-w-sm break-words text-sm leading-5 text-onSurface-default-secondary">
          {description}
        </p>
      </div>
    </div>
  );
}

export default function GraphPage() {
  const { language, t } = useI18n();
  const copy = pageCopy(language);
  const [syncing, setSyncing] = useState(false);
  const {
    data: graph,
    isLoading,
    error: graphError,
    refetch,
  } = useApiQuery<GraphResponse>(
    async () => {
      const res = await api.get<GraphResponse>(
        `${GRAPH_ENDPOINTS.BASE}?limit=800`,
      );
      return res.data;
    },
    {
      errorToast: "Failed to load graph",
      initialData: { configured: false, nodes: [], edges: [] },
    },
  );

  const {
    data: graphEntities,
    error: entityError,
    refetch: refetchEntities,
  } = useApiQuery<{ results: GraphEntity[] }>(
    async () => {
      const res = await api.get<{ results: GraphEntity[] }>(
        `${GRAPH_ENDPOINTS.ENTITIES}?limit=160`,
      );
      return res.data;
    },
    {
      errorToast: "Failed to load graph entities",
      initialData: { results: [] },
    },
  );

  const nodes = graph?.nodes ?? [];
  const status = graph?.status;
  const entities = graphEntities?.results ?? [];

  const sync = async () => {
    if (!graph?.configured || syncing) return;
    setSyncing(true);
    try {
      await api.post(GRAPH_ENDPOINTS.SYNC);
      await refetch();
      await refetchEntities();
      toast({ title: t("Graph synced"), variant: "success" });
    } catch (error) {
      toast({
        title: t("Failed to sync graph"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="min-w-0 space-y-4">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold font-fustat">{copy.title}</h1>
          <p className="mt-1 truncate text-xs text-onSurface-default-tertiary">
            {copy.project}
            {status?.project_id ? ` / ${status.project_id}` : ""}
          </p>
        </div>
        <Button
          variant="outline"
          onClick={sync}
          disabled={!graph?.configured || isLoading || syncing}
          className="self-start sm:self-auto"
        >
          <RefreshCw
            className={`mr-2 size-4 ${syncing ? "animate-spin" : ""}`}
          />
          {syncing ? copy.syncing : copy.sync}
        </Button>
      </header>

      {entityError && !graphError ? (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200">
          {copy.entityError}: {entityError}
        </div>
      ) : null}

      {isLoading ? (
        <GraphLoading />
      ) : graphError ? (
        <GraphState title={copy.loadError} description={graphError} danger />
      ) : !graph?.configured ? (
        <GraphState
          title={copy.notEnabled}
          description={copy.notEnabledDescription}
        />
      ) : nodes.length === 0 ? (
        <GraphState
          title={status?.reachable === false ? copy.unavailable : copy.empty}
          description={
            status?.last_error ? t(status.last_error) : copy.emptyDescription
          }
          danger={status?.reachable === false}
        />
      ) : (
        <GalaxyGraph
          nodes={nodes}
          edges={graph.edges ?? []}
          entities={entities}
          status={status}
          language={language}
        />
      )}
    </div>
  );
}
