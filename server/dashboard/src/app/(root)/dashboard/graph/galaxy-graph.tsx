// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, {
  type ForceGraphMethods,
  type LinkObject,
  type NodeObject,
} from "react-force-graph-2d";
import {
  Eye,
  EyeOff,
  Maximize2,
  PanelRight,
  Search,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { Language } from "@/lib/i18n";
import type {
  GraphEdge,
  GraphEntity,
  GraphNode,
  GraphStatus,
} from "@/types/api";

type GraphGroup = "memory" | "entity" | "scope" | "category";
type GraphFilter = "all" | GraphGroup;

interface PropertyNode extends GraphNode {
  group: GraphGroup;
  degree: number;
  color: string;
  borderColor: string;
  x?: number;
  y?: number;
}

interface PropertyLink extends Omit<GraphEdge, "source" | "target"> {
  id: string;
  source: string | PropertyNode;
  target: string | PropertyNode;
}

interface PropertyGraphProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  entities: GraphEntity[];
  status?: GraphStatus;
  language: Language;
}

const GROUP_STYLE: Record<GraphGroup, { color: string; borderColor: string }> =
  {
    memory: { color: "#a8c7fa", borderColor: "#3569a8" },
    entity: { color: "#8bd7c7", borderColor: "#28796b" },
    scope: { color: "#f7d488", borderColor: "#9a6818" },
    category: { color: "#f2a8b8", borderColor: "#9d4359" },
  };

const CANVAS_BACKGROUND = "#f4f6f8";

function graphCopy(language: Language) {
  if (language === "zh") {
    return {
      search: "\u641c\u7d22\u8282\u70b9",
      searchPlaceholder: "\u641c\u7d22\u8bb0\u5fc6\u6216\u5b9e\u4f53",
      noResults: "\u6ca1\u6709\u5339\u914d\u7684\u8282\u70b9",
      all: "\u5168\u90e8",
      memory: "\u8bb0\u5fc6",
      entity: "\u5b9e\u4f53",
      scope: "\u4f5c\u7528\u57df",
      category: "\u5206\u7c7b",
      nodes: "\u8282\u70b9",
      links: "\u5173\u7cfb",
      connected: "\u76f8\u8fde\u8282\u70b9",
      connectionCount: "\u8fde\u63a5\u6570",
      nodeId: "\u8282\u70b9 ID",
      nodeLabel: "\u6807\u7b7e",
      nodeKind: "\u7c7b\u578b",
      entityRank: "\u5b9e\u4f53\u70ed\u5ea6",
      memories: "\u8bb0\u5fc6",
      entities: "\u5b9e\u4f53",
      relationships: "\u5173\u7cfb",
      visible: "\u5f53\u524d\u53ef\u89c1",
      close: "\u5173\u95ed\u9762\u677f",
      openPanel: "\u6253\u5f00\u56fe\u8c31\u9762\u677f",
      zoomIn: "\u653e\u5927",
      zoomOut: "\u7f29\u5c0f",
      fit: "\u9002\u5e94\u89c6\u56fe",
      showLinks: "\u663e\u793a\u5173\u7cfb",
      hideLinks: "\u9690\u85cf\u5173\u7cfb",
      graphOnline: "Neo4j \u5728\u7ebf",
      graphOffline: "Neo4j \u5f02\u5e38",
      filter: "\u8282\u70b9\u7c7b\u578b",
      relationship: "\u5173\u7cfb",
      relationshipType: "\u5173\u7cfb\u7c7b\u578b",
      source: "\u8d77\u70b9",
      target: "\u7ec8\u70b9",
      weight: "\u6743\u91cd",
    };
  }

  return {
    search: "Search nodes",
    searchPlaceholder: "Search memories or entities",
    noResults: "No matching nodes",
    all: "All",
    memory: "Memory",
    entity: "Entity",
    scope: "Scope",
    category: "Category",
    nodes: "Nodes",
    links: "Relationships",
    connected: "Connected nodes",
    connectionCount: "Connections",
    nodeId: "Node ID",
    nodeLabel: "Label",
    nodeKind: "Type",
    entityRank: "Entity activity",
    memories: "Memories",
    entities: "Entities",
    relationships: "Relationships",
    visible: "Visible",
    close: "Close panel",
    openPanel: "Open graph panel",
    zoomIn: "Zoom in",
    zoomOut: "Zoom out",
    fit: "Fit graph",
    showLinks: "Show relationships",
    hideLinks: "Hide relationships",
    graphOnline: "Neo4j online",
    graphOffline: "Neo4j unavailable",
    filter: "Node type",
    relationship: "Relationship",
    relationshipType: "Relationship type",
    source: "Source",
    target: "Target",
    weight: "Weight",
  };
}

function classifyNode(node: GraphNode): GraphGroup {
  const label = node.label.toLowerCase();
  const kind = (node.kind || "").toLowerCase();

  if (label === "memory" || kind === "memory") return "memory";
  if (label === "category" || kind === "category" || kind === "topic") {
    return "category";
  }
  if (["scope", "user", "agent", "app", "run"].includes(kind)) {
    return "scope";
  }
  return "entity";
}

function hashUnit(value: string, salt = 0): number {
  let hash = 2166136261 ^ salt;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

function endpointId(endpoint: string | PropertyNode | undefined): string {
  if (!endpoint) return "";
  return typeof endpoint === "string" ? endpoint : endpoint.id;
}

function nodeRadius(node: PropertyNode): number {
  return Math.min(27, 13 + Math.sqrt(node.degree + 1) * 2.1);
}

function nodeDisplayTitle(node: GraphNode): string {
  if (node.title) return node.title;
  const structuralLabels = new Set(["memory", "entity", "category", "scope"]);
  return node.label && !structuralLabels.has(node.label.toLowerCase())
    ? node.label
    : node.id;
}

function shortLabel(value: string, max = 18): string[] {
  const normalized = value.trim().replace(/\s+/g, " ");
  if (normalized.length <= max) return [normalized];

  const splitAt = normalized.lastIndexOf(" ", max);
  if (splitAt > 5) {
    const first = normalized.slice(0, splitAt);
    const second = normalized.slice(splitAt + 1);
    return [
      first,
      second.length > max ? `${second.slice(0, max - 1)}...` : second,
    ];
  }
  return [normalized.slice(0, max), `${normalized.slice(max, max * 2 - 3)}...`];
}

function ControlButton({
  label,
  children,
  active = false,
  onClick,
}: {
  label: string;
  children: React.ReactNode;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={label}
          aria-pressed={active}
          onClick={onClick}
          className={`flex size-9 items-center justify-center border-b border-[#d7dce1] text-[#3d454d] transition-colors last:border-b-0 hover:bg-[#eef1f4] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#2f6fad] ${
            active ? "bg-[#e7f1fb] text-[#1e5d96]" : "bg-white"
          }`}
        >
          {children}
        </button>
      </TooltipTrigger>
      <TooltipContent side="left">{label}</TooltipContent>
    </Tooltip>
  );
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div className="min-w-0">
      <div className="text-base font-semibold tabular-nums text-[#20262c]">
        {value.toLocaleString()}
      </div>
      <div className="truncate text-[11px] text-[#69737d]">{label}</div>
    </div>
  );
}

function NodeInspector({
  node,
  neighbors,
  copy,
  onFocus,
  onClose,
}: {
  node: PropertyNode;
  neighbors: PropertyNode[];
  copy: ReturnType<typeof graphCopy>;
  onFocus: (node: PropertyNode) => void;
  onClose: () => void;
}) {
  return (
    <div
      className="flex min-h-0 flex-1 flex-col"
      data-testid="graph-node-inspector"
    >
      <div className="border-b border-[#dfe3e7] p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="mb-2 flex items-center gap-2 text-xs font-medium text-[#65717c]">
              <span
                className="size-2.5 shrink-0 rounded-full border"
                style={{
                  backgroundColor: node.color,
                  borderColor: node.borderColor,
                }}
              />
              {copy[node.group]}
            </div>
            <h2 className="break-words text-sm font-semibold leading-5 text-[#20262c]">
              {nodeDisplayTitle(node)}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={copy.close}
            className="flex size-8 shrink-0 items-center justify-center text-[#66717b] hover:bg-[#eef1f4] hover:text-[#20262c] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2f6fad]"
          >
            <X className="size-4" />
          </button>
        </div>

        <dl className="mt-4 space-y-2 border-t border-[#e3e6e9] pt-3 text-xs">
          <div className="grid grid-cols-[84px_minmax(0,1fr)] gap-2">
            <dt className="text-[#7a838c]">{copy.nodeLabel}</dt>
            <dd className="break-words font-medium text-[#303840]">
              {node.label}
            </dd>
          </div>
          <div className="grid grid-cols-[84px_minmax(0,1fr)] gap-2">
            <dt className="text-[#7a838c]">{copy.nodeKind}</dt>
            <dd className="break-words font-medium text-[#303840]">
              {node.kind || "-"}
            </dd>
          </div>
          <div className="grid grid-cols-[84px_minmax(0,1fr)] gap-2">
            <dt className="text-[#7a838c]">{copy.connectionCount}</dt>
            <dd className="font-medium tabular-nums text-[#303840]">
              {node.degree}
            </dd>
          </div>
        </dl>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="px-4 pb-2 pt-4 text-xs font-medium text-[#65717c]">
          {copy.connected}{" "}
          <span className="text-[#929aa1]">{neighbors.length}</span>
        </div>
        <div className="divide-y divide-[#e5e8eb]">
          {neighbors.slice(0, 24).map((neighbor) => (
            <button
              type="button"
              key={neighbor.id}
              onClick={() => onFocus(neighbor)}
              className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-[#f1f4f6] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#2f6fad]"
            >
              <span
                className="size-2.5 shrink-0 rounded-full border"
                style={{
                  backgroundColor: neighbor.color,
                  borderColor: neighbor.borderColor,
                }}
              />
              <span className="min-w-0 flex-1 truncate text-xs text-[#303840]">
                {nodeDisplayTitle(neighbor)}
              </span>
              <span className="text-[11px] tabular-nums text-[#7d8790]">
                {neighbor.degree}
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="border-t border-[#dfe3e7] p-4">
        <div className="text-[11px] text-[#7a838c]">{copy.nodeId}</div>
        <div className="mt-1 break-all font-mono text-[10px] leading-4 text-[#535f69]">
          {node.id}
        </div>
      </div>
    </div>
  );
}

function RelationshipInspector({
  link,
  source,
  target,
  copy,
  onFocus,
  onClose,
}: {
  link: PropertyLink;
  source?: PropertyNode;
  target?: PropertyNode;
  copy: ReturnType<typeof graphCopy>;
  onFocus: (node: PropertyNode) => void;
  onClose: () => void;
}) {
  return (
    <div
      className="flex min-h-0 flex-1 flex-col"
      data-testid="graph-relationship-inspector"
    >
      <div className="border-b border-[#dfe3e7] p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-xs font-medium text-[#65717c]">
              {copy.relationship}
            </div>
            <h2 className="mt-2 break-words text-sm font-semibold text-[#20262c]">
              {link.type}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={copy.close}
            className="flex size-8 shrink-0 items-center justify-center text-[#66717b] hover:bg-[#eef1f4] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2f6fad]"
          >
            <X className="size-4" />
          </button>
        </div>
      </div>

      <dl className="space-y-4 p-4 text-xs">
        <div>
          <dt className="text-[#7a838c]">{copy.relationshipType}</dt>
          <dd className="mt-1 font-mono font-medium text-[#303840]">
            {link.type}
          </dd>
        </div>
        <div>
          <dt className="text-[#7a838c]">{copy.source}</dt>
          <dd className="mt-1">
            {source ? (
              <button
                type="button"
                onClick={() => onFocus(source)}
                className="flex w-full items-center gap-2 border border-[#dfe3e7] bg-[#f7f9fa] px-3 py-2 text-left hover:border-[#9ebbd5] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2f6fad]"
              >
                <span
                  className="size-2.5 shrink-0 rounded-full border"
                  style={{
                    backgroundColor: source.color,
                    borderColor: source.borderColor,
                  }}
                />
                <span className="min-w-0 truncate text-[#303840]">
                  {nodeDisplayTitle(source)}
                </span>
              </button>
            ) : (
              endpointId(link.source)
            )}
          </dd>
        </div>
        <div>
          <dt className="text-[#7a838c]">{copy.target}</dt>
          <dd className="mt-1">
            {target ? (
              <button
                type="button"
                onClick={() => onFocus(target)}
                className="flex w-full items-center gap-2 border border-[#dfe3e7] bg-[#f7f9fa] px-3 py-2 text-left hover:border-[#9ebbd5] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2f6fad]"
              >
                <span
                  className="size-2.5 shrink-0 rounded-full border"
                  style={{
                    backgroundColor: target.color,
                    borderColor: target.borderColor,
                  }}
                />
                <span className="min-w-0 truncate text-[#303840]">
                  {nodeDisplayTitle(target)}
                </span>
              </button>
            ) : (
              endpointId(link.target)
            )}
          </dd>
        </div>
        {link.weight ? (
          <div>
            <dt className="text-[#7a838c]">{copy.weight}</dt>
            <dd className="mt-1 font-medium text-[#303840]">{link.weight}</dd>
          </div>
        ) : null}
      </dl>
    </div>
  );
}

function EntityNavigator({
  entities,
  status,
  visibleCount,
  copy,
  onEntityClick,
}: {
  entities: GraphEntity[];
  status?: GraphStatus;
  visibleCount: number;
  copy: ReturnType<typeof graphCopy>;
  onEntityClick: (entity: GraphEntity) => void;
}) {
  return (
    <div
      className="flex min-h-0 flex-1 flex-col"
      data-testid="graph-entity-panel"
    >
      <div className="border-b border-[#dfe3e7] p-4">
        <div className="flex items-center gap-2 text-xs text-[#56616b]">
          <span
            className={`size-2 rounded-full ${status?.reachable === false ? "bg-red-500" : "bg-emerald-500"}`}
          />
          {status?.reachable === false ? copy.graphOffline : copy.graphOnline}
        </div>
        <div className="mt-4 grid grid-cols-2 gap-x-5 gap-y-4">
          <Stat value={status?.memories ?? 0} label={copy.memories} />
          <Stat value={status?.entities ?? 0} label={copy.entities} />
          <Stat value={status?.relationships ?? 0} label={copy.relationships} />
          <Stat value={visibleCount} label={copy.visible} />
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="px-4 pb-2 pt-4 text-xs font-medium text-[#65717c]">
          {copy.entityRank}
        </div>
        <div className="min-h-0 flex-1 divide-y divide-[#e5e8eb] overflow-y-auto">
          {entities.slice(0, 80).map((entity, index) => (
            <button
              type="button"
              key={entity.norm}
              onClick={() => onEntityClick(entity)}
              className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-[#f1f4f6] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#2f6fad]"
            >
              <span className="w-5 shrink-0 text-[10px] tabular-nums text-[#9aa2a9]">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs text-[#303840]">
                  {entity.name}
                </span>
                <span className="mt-0.5 block truncate text-[10px] text-[#7d8790]">
                  {entity.type}
                </span>
              </span>
              <span className="text-[11px] tabular-nums text-[#65717c]">
                {entity.memory_count}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function PropertyGraph({
  nodes,
  edges,
  entities,
  status,
  language,
}: PropertyGraphProps) {
  const copy = useMemo(() => graphCopy(language), [language]);
  const graphRef = useRef<
    ForceGraphMethods<PropertyNode, PropertyLink> | undefined
  >(undefined);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const fittedRef = useRef(false);
  const [size, setSize] = useState({ width: 900, height: 680 });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedLinkId, setSelectedLinkId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<GraphFilter>("all");
  const [showLinks, setShowLinks] = useState(true);
  const [mobilePanelOpen, setMobilePanelOpen] = useState(false);

  const { graphNodes, graphLinks, adjacency } = useMemo(() => {
    const degree = new Map<string, number>();
    const adjacent = new Map<string, Set<string>>();

    nodes.forEach((node) => {
      degree.set(node.id, 0);
      adjacent.set(node.id, new Set());
    });
    edges.forEach((edge) => {
      degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
      degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
      adjacent.get(edge.source)?.add(edge.target);
      adjacent.get(edge.target)?.add(edge.source);
    });

    const preparedNodes: PropertyNode[] = nodes.map((node) => {
      const group = classifyNode(node);
      const angle = hashUnit(node.id, 17) * Math.PI * 2;
      const radius = 80 + hashUnit(node.id, 31) * 260;
      return {
        ...node,
        group,
        degree: degree.get(node.id) ?? 0,
        ...GROUP_STYLE[group],
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius,
      };
    });

    return {
      graphNodes: preparedNodes,
      graphLinks: edges.map((edge, index) => ({
        ...edge,
        id: `${edge.source}:${edge.type}:${edge.target}:${index}`,
      })) as PropertyLink[],
      adjacency: adjacent,
    };
  }, [edges, nodes]);

  const nodesById = useMemo(
    () => new Map(graphNodes.map((node) => [node.id, node])),
    [graphNodes],
  );
  const linksById = useMemo(
    () => new Map(graphLinks.map((link) => [link.id, link])),
    [graphLinks],
  );
  // Hover styling re-renders this component. Keep the data identity stable so
  // the force renderer does not restart its simulation on every pointer move.
  const graphData = useMemo(
    () => ({ nodes: graphNodes, links: graphLinks }),
    [graphLinks, graphNodes],
  );
  const selectedNode = selectedId ? (nodesById.get(selectedId) ?? null) : null;
  const selectedLink = selectedLinkId
    ? (linksById.get(selectedLinkId) ?? null)
    : null;
  const selectedNeighborIds = useMemo(
    () =>
      selectedId
        ? (adjacency.get(selectedId) ?? new Set<string>())
        : new Set<string>(),
    [adjacency, selectedId],
  );
  const selectedNeighbors = useMemo(
    () =>
      Array.from(selectedNeighborIds)
        .map((id) => nodesById.get(id))
        .filter((node): node is PropertyNode => Boolean(node))
        .sort((a, b) => b.degree - a.degree),
    [nodesById, selectedNeighborIds],
  );
  const visibleNodes = useMemo(
    () =>
      graphNodes.filter((node) => filter === "all" || node.group === filter),
    [filter, graphNodes],
  );
  const searchResults = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return [];
    return graphNodes
      .filter((node) =>
        `${nodeDisplayTitle(node)} ${node.id} ${node.kind || ""}`
          .toLocaleLowerCase()
          .includes(normalized),
      )
      .sort((a, b) => b.degree - a.degree)
      .slice(0, 8);
  }, [graphNodes, query]);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({
        width: Math.max(280, Math.round(entry.contentRect.width)),
        height: Math.max(480, Math.round(entry.contentRect.height)),
      });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    graph.d3Force("charge")?.strength?.(-150);
    graph
      .d3Force("link")
      ?.distance?.((link: PropertyLink) =>
        link.type === "RELATED_TO"
          ? 105
          : link.type === "IN_CATEGORY"
            ? 78
            : 68,
      );
    fittedRef.current = false;
    graph.d3ReheatSimulation();
  }, [graphNodes]);

  const isNodeVisible = useCallback(
    (node: NodeObject<PropertyNode>) =>
      filter === "all" || node.group === filter,
    [filter],
  );

  const isLinkVisible = useCallback(
    (link: LinkObject<PropertyNode, PropertyLink>) => {
      if (!showLinks) return false;
      const source = nodesById.get(
        endpointId(link.source as string | PropertyNode),
      );
      const target = nodesById.get(
        endpointId(link.target as string | PropertyNode),
      );
      if (!source || !target) return false;
      return (
        filter === "all" || (source.group === filter && target.group === filter)
      );
    },
    [filter, nodesById, showLinks],
  );

  const focusNode = useCallback((node: PropertyNode) => {
    setSelectedId(node.id);
    setSelectedLinkId(null);
    setMobilePanelOpen(true);
    const graph = graphRef.current;
    graph?.centerAt(node.x ?? 0, node.y ?? 0, 650);
    graph?.zoom(Math.max(graph.zoom(), 2.3), 650);
  }, []);

  const fitGraph = useCallback(() => {
    setSelectedId(null);
    setSelectedLinkId(null);
    graphRef.current?.zoomToFit(700, 64, (node) => isNodeVisible(node));
  }, [isNodeVisible]);

  const zoomBy = useCallback((factor: number) => {
    const graph = graphRef.current;
    if (!graph) return;
    graph.zoom(graph.zoom() * factor, 260);
  }, []);

  const focusEntity = useCallback(
    (entity: GraphEntity) => {
      const normalizedName = entity.name.toLocaleLowerCase();
      const match = graphNodes.find(
        (node) =>
          node.title?.toLocaleLowerCase() === normalizedName ||
          node.id.toLocaleLowerCase().includes(entity.norm.toLocaleLowerCase()),
      );
      if (match) focusNode(match);
      else setQuery(entity.name);
    },
    [focusNode, graphNodes],
  );

  const drawNode = useCallback(
    (
      node: NodeObject<PropertyNode>,
      context: CanvasRenderingContext2D,
      globalScale: number,
    ) => {
      const radius = nodeRadius(node);
      const selected = node.id === selectedId;
      const hovered = node.id === hoveredId;
      const dimmed = Boolean(
        selectedId && !selected && !selectedNeighborIds.has(node.id),
      );

      context.save();
      context.globalAlpha = dimmed ? 0.22 : 1;
      if (selected || hovered) {
        context.beginPath();
        context.arc(node.x ?? 0, node.y ?? 0, radius + 4.5, 0, Math.PI * 2);
        context.fillStyle = selected
          ? "rgba(47, 111, 173, 0.2)"
          : "rgba(32, 38, 44, 0.12)";
        context.fill();
      }
      context.beginPath();
      context.arc(node.x ?? 0, node.y ?? 0, radius, 0, Math.PI * 2);
      context.fillStyle = node.color;
      context.fill();
      context.strokeStyle = selected ? "#174f83" : node.borderColor;
      context.lineWidth = (selected ? 3 : 1.6) / globalScale;
      context.stroke();

      const lines = shortLabel(nodeDisplayTitle(node));
      const fontSize = Math.max(3.2, Math.min(4.5, radius * 0.25));
      context.font = `600 ${fontSize}px Arial, sans-serif`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillStyle = "#182026";
      const lineHeight = fontSize * 1.18;
      lines.slice(0, 2).forEach((line, index) => {
        context.fillText(
          line,
          node.x ?? 0,
          (node.y ?? 0) + (index - (lines.length - 1) / 2) * lineHeight,
          radius * 1.65,
        );
      });
      context.restore();
    },
    [hoveredId, selectedId, selectedNeighborIds],
  );

  const paintNodeArea = useCallback(
    (
      node: NodeObject<PropertyNode>,
      color: string,
      context: CanvasRenderingContext2D,
    ) => {
      context.beginPath();
      context.arc(
        node.x ?? 0,
        node.y ?? 0,
        nodeRadius(node) + 3,
        0,
        Math.PI * 2,
      );
      context.fillStyle = color;
      context.fill();
    },
    [],
  );

  const drawLinkLabel = useCallback(
    (
      link: LinkObject<PropertyNode, PropertyLink>,
      context: CanvasRenderingContext2D,
      globalScale: number,
    ) => {
      if (!showLinks || globalScale < 0.72) return;
      const source = link.source as PropertyNode;
      const target = link.target as PropertyNode;
      if (
        source.x == null ||
        source.y == null ||
        target.x == null ||
        target.y == null
      )
        return;
      const isSelected = link.id === selectedLinkId;
      const connectedToSelection =
        selectedId && (source.id === selectedId || target.id === selectedId);
      if (selectedId && !connectedToSelection) return;

      const x = (source.x + target.x) / 2;
      const y = (source.y + target.y) / 2;
      const fontSize = 3.5;
      context.save();
      context.font = `600 ${fontSize}px Arial, sans-serif`;
      const text = link.type || "RELATED";
      const width = context.measureText(text).width + 4.5;
      context.fillStyle = isSelected ? "#dcecff" : "rgba(255,255,255,0.94)";
      context.strokeStyle = isSelected ? "#2f6fad" : "#aeb6bd";
      context.lineWidth = 1 / globalScale;
      context.beginPath();
      context.rect(x - width / 2, y - fontSize, width, fontSize * 2);
      context.fill();
      context.stroke();
      context.fillStyle = isSelected ? "#174f83" : "#4e5963";
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(text, x, y, width - 2);
      context.restore();
    },
    [selectedId, selectedLinkId, showLinks],
  );

  const paintLinkArea = useCallback(
    (
      link: LinkObject<PropertyNode, PropertyLink>,
      color: string,
      context: CanvasRenderingContext2D,
      globalScale: number,
    ) => {
      const source = link.source as PropertyNode;
      const target = link.target as PropertyNode;
      if (
        source.x == null ||
        source.y == null ||
        target.x == null ||
        target.y == null
      )
        return;
      context.beginPath();
      context.moveTo(source.x, source.y);
      context.lineTo(target.x, target.y);
      context.strokeStyle = color;
      context.lineWidth = Math.max(5 / globalScale, 2);
      context.stroke();
    },
    [],
  );

  const renderPanel = selectedNode ? (
    <NodeInspector
      node={selectedNode}
      neighbors={selectedNeighbors}
      copy={copy}
      onFocus={focusNode}
      onClose={() => {
        setSelectedId(null);
        setMobilePanelOpen(false);
      }}
    />
  ) : selectedLink ? (
    <RelationshipInspector
      link={selectedLink}
      source={nodesById.get(endpointId(selectedLink.source))}
      target={nodesById.get(endpointId(selectedLink.target))}
      copy={copy}
      onFocus={focusNode}
      onClose={() => {
        setSelectedLinkId(null);
        setMobilePanelOpen(false);
      }}
    />
  ) : (
    <EntityNavigator
      entities={entities}
      status={status}
      visibleCount={visibleNodes.length}
      copy={copy}
      onEntityClick={focusEntity}
    />
  );

  return (
    <section
      className="relative isolate h-[calc(100dvh-190px)] min-h-[480px] max-h-[920px] overflow-hidden rounded-md border border-memBorder-primary bg-[#f4f6f8] shadow-sm sm:h-[calc(100dvh-176px)] sm:min-h-[620px]"
      aria-label={
        language === "zh"
          ? "\u4ea4\u4e92\u5f0f\u8bb0\u5fc6\u56fe\u8c31"
          : "Interactive memory graph"
      }
      data-testid="memory-graph"
    >
      <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_296px]">
        <div
          ref={containerRef}
          className="relative min-h-0 overflow-hidden bg-[#f4f6f8]"
        >
          <ForceGraph2D<PropertyNode, PropertyLink>
            ref={graphRef}
            width={size.width}
            height={size.height}
            graphData={graphData}
            backgroundColor={CANVAS_BACKGROUND}
            nodeId="id"
            nodeLabel={(node) => nodeDisplayTitle(node)}
            nodeVisibility={isNodeVisible}
            nodeCanvasObjectMode={() => "replace"}
            nodeCanvasObject={drawNode}
            nodePointerAreaPaint={paintNodeArea}
            linkVisibility={isLinkVisible}
            linkLabel={(link) => link.type}
            linkColor={(link) => {
              if (link.id === selectedLinkId) return "#2f6fad";
              const sourceId = endpointId(link.source as string | PropertyNode);
              const targetId = endpointId(link.target as string | PropertyNode);
              if (
                selectedId &&
                (sourceId === selectedId || targetId === selectedId)
              )
                return "#6587a5";
              return selectedId ? "rgba(130,140,149,0.16)" : "#aeb6bd";
            }}
            linkWidth={(link) => (link.id === selectedLinkId ? 2.5 : 1.15)}
            linkDirectionalArrowLength={5}
            linkDirectionalArrowRelPos={0.86}
            linkDirectionalArrowColor={(link) =>
              link.id === selectedLinkId ? "#2f6fad" : "#7c878f"
            }
            linkCanvasObjectMode={() => "after"}
            linkCanvasObject={drawLinkLabel}
            linkPointerAreaPaint={paintLinkArea}
            minZoom={0.18}
            maxZoom={8}
            warmupTicks={70}
            cooldownTicks={160}
            d3VelocityDecay={0.32}
            enableNodeDrag
            enablePanInteraction
            enableZoomInteraction
            onNodeClick={(node) => focusNode(node)}
            onNodeHover={(node) => setHoveredId(node?.id ?? null)}
            onLinkClick={(link) => {
              setSelectedId(null);
              setSelectedLinkId(link.id);
              setMobilePanelOpen(true);
            }}
            onBackgroundClick={() => {
              setSelectedId(null);
              setSelectedLinkId(null);
            }}
            onEngineStop={() => {
              if (fittedRef.current) return;
              fittedRef.current = true;
              graphRef.current?.zoomToFit(700, 64, (node) =>
                isNodeVisible(node),
              );
            }}
          />

          <div className="pointer-events-none absolute inset-x-0 top-0 z-10 h-[60px] border-b border-[#e2e6e9] bg-[#f4f6f8]/95" />

          <div className="pointer-events-none absolute inset-x-3 top-3 z-20 flex items-start justify-between gap-3">
            <div className="pointer-events-auto relative min-w-0 flex-1 sm:max-w-[360px]">
              <Search className="pointer-events-none absolute left-3 top-1/2 z-10 size-4 -translate-y-1/2 text-[#74808a]" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && searchResults[0]) {
                    focusNode(searchResults[0]);
                    setQuery("");
                  }
                  if (event.key === "Escape") setQuery("");
                }}
                aria-label={copy.search}
                placeholder={copy.searchPlaceholder}
                className="h-10 w-full border border-[#cbd1d6] bg-white/95 pl-9 pr-9 text-sm text-[#20262c] shadow-sm outline-none placeholder:text-[#87919a] focus:border-[#2f6fad] focus:ring-1 focus:ring-[#2f6fad]"
              />
              {query ? (
                <button
                  type="button"
                  aria-label={copy.close}
                  onClick={() => setQuery("")}
                  className="absolute right-1 top-1 flex size-8 items-center justify-center text-[#74808a] hover:text-[#20262c] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2f6fad]"
                >
                  <X className="size-4" />
                </button>
              ) : null}
              {query.trim() ? (
                <div className="absolute inset-x-0 top-11 max-h-72 overflow-y-auto border border-[#cbd1d6] bg-white shadow-xl">
                  {searchResults.length ? (
                    searchResults.map((node) => (
                      <button
                        type="button"
                        key={node.id}
                        onClick={() => {
                          focusNode(node);
                          setQuery("");
                        }}
                        className="flex w-full items-center gap-3 border-b border-[#e5e8eb] px-3 py-2.5 text-left last:border-b-0 hover:bg-[#f1f4f6] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#2f6fad]"
                      >
                        <span
                          className="size-2.5 shrink-0 rounded-full border"
                          style={{
                            backgroundColor: node.color,
                            borderColor: node.borderColor,
                          }}
                        />
                        <span className="min-w-0 flex-1 truncate text-xs text-[#303840]">
                          {nodeDisplayTitle(node)}
                        </span>
                        <span className="text-[10px] text-[#77818a]">
                          {copy[node.group]}
                        </span>
                      </button>
                    ))
                  ) : (
                    <div className="px-3 py-6 text-center text-xs text-[#77818a]">
                      {copy.noResults}
                    </div>
                  )}
                </div>
              ) : null}
            </div>

            <button
              type="button"
              aria-label={copy.openPanel}
              onClick={() => setMobilePanelOpen(true)}
              className="pointer-events-auto flex size-10 shrink-0 items-center justify-center border border-[#cbd1d6] bg-white/95 text-[#56616b] shadow-sm hover:bg-[#eef1f4] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2f6fad] lg:hidden"
            >
              <PanelRight className="size-4" />
            </button>
          </div>

          <div className="absolute left-3 top-[64px] z-10 flex items-center gap-3 text-[11px] text-[#65717c] lg:hidden">
            <span>
              {visibleNodes.length} {copy.nodes}
            </span>
            <span>
              {edges.length} {copy.links}
            </span>
          </div>

          <div
            className={`absolute right-3 top-1/2 z-20 -translate-y-1/2 overflow-hidden border border-[#cbd1d6] bg-white shadow-md ${mobilePanelOpen ? "hidden lg:block" : ""}`}
          >
            <ControlButton label={copy.zoomIn} onClick={() => zoomBy(1.35)}>
              <ZoomIn className="size-4" />
            </ControlButton>
            <ControlButton label={copy.zoomOut} onClick={() => zoomBy(0.74)}>
              <ZoomOut className="size-4" />
            </ControlButton>
            <ControlButton label={copy.fit} onClick={fitGraph}>
              <Maximize2 className="size-4" />
            </ControlButton>
            <ControlButton
              label={showLinks ? copy.hideLinks : copy.showLinks}
              active={showLinks}
              onClick={() => setShowLinks((value) => !value)}
            >
              {showLinks ? (
                <Eye className="size-4" />
              ) : (
                <EyeOff className="size-4" />
              )}
            </ControlButton>
          </div>

          <div
            className={`absolute bottom-3 left-3 z-20 flex max-w-[calc(100%-60px)] items-center gap-2 overflow-x-auto ${mobilePanelOpen ? "hidden lg:flex" : ""}`}
          >
            <div
              className="flex h-9 shrink-0 overflow-hidden border border-[#cbd1d6] bg-white shadow-sm"
              role="group"
              aria-label={copy.filter}
            >
              {(["all", "memory", "entity", "scope", "category"] as const).map(
                (value) => (
                  <button
                    type="button"
                    key={value}
                    onClick={() => setFilter(value)}
                    aria-pressed={filter === value}
                    className={`flex items-center gap-1.5 border-r border-[#dfe3e7] px-2.5 text-[11px] last:border-r-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#2f6fad] ${
                      filter === value
                        ? "bg-[#e7f1fb] font-medium text-[#174f83]"
                        : "bg-white text-[#56616b] hover:bg-[#f1f4f6]"
                    }`}
                  >
                    {value !== "all" ? (
                      <span
                        className="size-2 rounded-full border"
                        style={{
                          backgroundColor: GROUP_STYLE[value].color,
                          borderColor: GROUP_STYLE[value].borderColor,
                        }}
                      />
                    ) : null}
                    {copy[value]}
                  </button>
                ),
              )}
            </div>
          </div>
        </div>

        <aside className="hidden min-h-0 border-l border-[#d7dce1] bg-white lg:flex">
          {renderPanel}
        </aside>
      </div>

      {mobilePanelOpen ? (
        <div className="absolute inset-x-0 bottom-0 z-40 flex h-[62%] min-h-[280px] border-t border-[#cbd1d6] bg-white shadow-[0_-18px_45px_rgba(35,44,52,0.18)] sm:h-[58%] sm:min-h-[340px] lg:hidden">
          {!selectedNode && !selectedLink ? (
            <button
              type="button"
              aria-label={copy.close}
              onClick={() => setMobilePanelOpen(false)}
              className="absolute right-3 top-3 z-10 flex size-8 items-center justify-center text-[#66717b] hover:bg-[#eef1f4] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2f6fad]"
            >
              <X className="size-4" />
            </button>
          ) : null}
          {renderPanel}
        </div>
      ) : null}
    </section>
  );
}
