// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D, {
  type ForceGraphMethods,
  type LinkObject,
  type NodeObject,
} from "react-force-graph-3d";
import * as THREE from "three";
import {
  Eye,
  EyeOff,
  Maximize2,
  Orbit,
  PanelRight,
  Pause,
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
import type {
  GraphEdge,
  GraphEntity,
  GraphNode,
  GraphStatus,
} from "@/types/api";
import type { Language } from "@/lib/i18n";

type GalaxyGroup = "memory" | "entity" | "scope" | "category";
type GalaxyFilter = "all" | GalaxyGroup;

interface GalaxyNode extends GraphNode {
  group: GalaxyGroup;
  degree: number;
  color: string;
  x?: number;
  y?: number;
  z?: number;
}

interface GalaxyLink extends Omit<GraphEdge, "source" | "target"> {
  source: string | GalaxyNode;
  target: string | GalaxyNode;
}

interface OrbitControlsLike {
  autoRotate: boolean;
  autoRotateSpeed: number;
  enableDamping: boolean;
  dampingFactor: number;
  update?: () => void;
}

interface GalaxyGraphProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  entities: GraphEntity[];
  status?: GraphStatus;
  language: Language;
}

const GROUP_COLORS: Record<GalaxyGroup, string> = {
  memory: "#f1f4ff",
  entity: "#67d7e8",
  scope: "#f1c86a",
  category: "#ff8976",
};

const BACKGROUND = "#05070a";

function graphCopy(language: Language) {
  if (language === "zh") {
    return {
      search: "\u641c\u7d22\u8282\u70b9",
      searchPlaceholder: "\u641c\u7d22\u8bb0\u5fc6\u6216\u5b9e\u4f53",
      noResults: "\u6ca1\u6709\u5339\u914d\u7684\u8282\u70b9",
      all: "\u5168\u90e8\u8282\u70b9",
      memory: "\u8bb0\u5fc6",
      entity: "\u5b9e\u4f53",
      scope: "\u4f5c\u7528\u57df",
      category: "\u5206\u7c7b",
      nodes: "\u8282\u70b9",
      links: "\u5173\u7cfb",
      connected: "\u76f8\u8fde\u8282\u70b9",
      connectionCount: "\u8fde\u63a5\u6570",
      nodeId: "\u8282\u70b9 ID",
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
      startOrbit: "\u5f00\u59cb\u6f2b\u6e38",
      stopOrbit: "\u6682\u505c\u6f2b\u6e38",
      showLinks: "\u663e\u793a\u5173\u7cfb\u7ebf",
      hideLinks: "\u9690\u85cf\u5173\u7cfb\u7ebf",
      graphOnline: "Neo4j \u5728\u7ebf",
      graphOffline: "Neo4j \u5f02\u5e38",
      filter: "\u8282\u70b9\u7c7b\u578b",
    };
  }

  return {
    search: "Search nodes",
    searchPlaceholder: "Search memories or entities",
    noResults: "No matching nodes",
    all: "All nodes",
    memory: "Memory",
    entity: "Entity",
    scope: "Scope",
    category: "Category",
    nodes: "Nodes",
    links: "Links",
    connected: "Connected nodes",
    connectionCount: "Connections",
    nodeId: "Node ID",
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
    startOrbit: "Start orbit",
    stopOrbit: "Pause orbit",
    showLinks: "Show links",
    hideLinks: "Hide links",
    graphOnline: "Neo4j online",
    graphOffline: "Neo4j unavailable",
    filter: "Node type",
  };
}

function classifyNode(node: GraphNode): GalaxyGroup {
  const label = node.label.toLowerCase();
  const kind = (node.kind || "").toLowerCase();

  if (label === "memory") return "memory";
  if (label === "category" || kind === "category" || kind === "topic") {
    return "category";
  }
  if (["scope", "user", "agent", "app", "run"].includes(kind)) {
    return "scope";
  }
  if (label === "entity") return "entity";
  if (kind === "memory") return "memory";
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

function seededRandom(seed: number) {
  let state = seed >>> 0;
  return () => {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function endpointId(endpoint: string | GalaxyNode): string {
  return typeof endpoint === "string" ? endpoint : endpoint.id;
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function truncate(value: string, max = 72) {
  return value.length > max ? `${value.slice(0, max - 1)}...` : value;
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
          className={`flex size-9 items-center justify-center border-b border-white/10 text-white transition-colors last:border-b-0 hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-300 ${
            active ? "bg-cyan-300/15 text-cyan-200" : "bg-transparent"
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
      <div className="text-base font-semibold tabular-nums text-white">
        {value.toLocaleString()}
      </div>
      <div className="truncate text-[11px] text-white/45">{label}</div>
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
  node: GalaxyNode;
  neighbors: GalaxyNode[];
  copy: ReturnType<typeof graphCopy>;
  onFocus: (node: GalaxyNode) => void;
  onClose: () => void;
}) {
  return (
    <div
      className="flex min-h-0 flex-1 flex-col"
      data-testid="graph-node-inspector"
    >
      <div className="border-b border-white/10 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="mb-2 flex items-center gap-2 text-xs text-white/55">
              <span
                className="size-2 shrink-0 rounded-full"
                style={{ backgroundColor: node.color }}
              />
              {copy[node.group]}
            </div>
            <h2 className="break-words text-sm font-semibold leading-5 text-white">
              {node.title || node.id}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={copy.close}
            className="flex size-8 shrink-0 items-center justify-center text-white/55 hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300"
          >
            <X className="size-4" />
          </button>
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-white/10 pt-3">
          <div>
            <dt className="text-[11px] text-white/40">
              {copy.connectionCount}
            </dt>
            <dd className="mt-1 text-sm font-medium text-white">
              {node.degree}
            </dd>
          </div>
          <div>
            <dt className="text-[11px] text-white/40">{copy.nodes}</dt>
            <dd className="mt-1 text-sm font-medium text-white">
              {node.label}
            </dd>
          </div>
        </dl>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="px-4 pb-2 pt-4 text-xs font-medium text-white/55">
          {copy.connected}{" "}
          <span className="text-white/30">{neighbors.length}</span>
        </div>
        <div className="divide-y divide-white/10">
          {neighbors.slice(0, 24).map((neighbor) => (
            <button
              type="button"
              key={neighbor.id}
              onClick={() => onFocus(neighbor)}
              className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-300"
            >
              <span
                className="size-2 shrink-0 rounded-full"
                style={{ backgroundColor: neighbor.color }}
              />
              <span className="min-w-0 flex-1 truncate text-xs text-white/80">
                {neighbor.title || neighbor.id}
              </span>
              <span className="text-[11px] tabular-nums text-white/35">
                {neighbor.degree}
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="border-t border-white/10 p-4">
        <div className="text-[11px] text-white/40">{copy.nodeId}</div>
        <div className="mt-1 break-all font-mono text-[10px] leading-4 text-white/55">
          {node.id}
        </div>
      </div>
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
      <div className="border-b border-white/10 p-4">
        <div className="flex items-center gap-2 text-xs text-white/60">
          <span
            className={`size-2 rounded-full ${
              status?.reachable === false ? "bg-red-400" : "bg-emerald-400"
            }`}
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
        <div className="px-4 pb-2 pt-4 text-xs font-medium text-white/55">
          {copy.entityRank}
        </div>
        <div className="min-h-0 flex-1 divide-y divide-white/10 overflow-y-auto">
          {entities.slice(0, 80).map((entity, index) => (
            <button
              type="button"
              key={entity.norm}
              onClick={() => onEntityClick(entity)}
              className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-300"
            >
              <span className="w-5 shrink-0 text-[10px] tabular-nums text-white/25">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs text-white/80">
                  {entity.name}
                </span>
                <span className="mt-0.5 block truncate text-[10px] text-white/35">
                  {entity.type}
                </span>
              </span>
              <span className="text-[11px] tabular-nums text-white/45">
                {entity.memory_count}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function GalaxyGraph({
  nodes,
  edges,
  entities,
  status,
  language,
}: GalaxyGraphProps) {
  const copy = useMemo(() => graphCopy(language), [language]);
  const graphRef = useRef<
    ForceGraphMethods<GalaxyNode, GalaxyLink> | undefined
  >(undefined);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const starFieldRef = useRef<THREE.Points | null>(null);
  const fittedRef = useRef(false);
  const [size, setSize] = useState({ width: 900, height: 680 });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<GalaxyFilter>("all");
  const [showLinks, setShowLinks] = useState(true);
  const [orbiting, setOrbiting] = useState(false);
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

    const preparedNodes: GalaxyNode[] = nodes.map((node, index) => {
      const group = classifyNode(node);
      const angle = hashUnit(node.id, 17) * Math.PI * 12;
      const groupRadius =
        group === "entity"
          ? 62
          : group === "category"
            ? 96
            : group === "scope"
              ? 150
              : 205;
      const radius = groupRadius * (0.58 + hashUnit(node.id, 31) * 0.7);
      const armOffset = (index % 4) * (Math.PI / 2);
      return {
        ...node,
        group,
        degree: degree.get(node.id) ?? 0,
        color: GROUP_COLORS[group],
        x: Math.cos(angle + armOffset) * radius,
        y: Math.sin(angle + armOffset) * radius,
        z: (hashUnit(node.id, 53) - 0.5) * (group === "memory" ? 105 : 65),
      };
    });

    return {
      graphNodes: preparedNodes,
      graphLinks: edges.map((edge) => ({ ...edge }) as GalaxyLink),
      adjacency: adjacent,
    };
  }, [edges, nodes]);

  const nodesById = useMemo(
    () => new Map(graphNodes.map((node) => [node.id, node])),
    [graphNodes],
  );
  const selectedNode = selectedId ? (nodesById.get(selectedId) ?? null) : null;
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
        .filter((node): node is GalaxyNode => Boolean(node))
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
        `${node.title || ""} ${node.id} ${node.kind || ""}`
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
      const nextWidth = Math.max(280, Math.round(entry.contentRect.width));
      const nextHeight = Math.max(560, Math.round(entry.contentRect.height));
      setSize({ width: nextWidth, height: nextHeight });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!graphRef.current || starFieldRef.current) return;
    const scene = graphRef.current.scene();
    const ambientCount = size.width < 640 ? 620 : 1250;
    const dustCount = size.width < 640 ? 320 : 700;
    const count = ambientCount + dustCount;
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const cool = new THREE.Color("#a8c8ff");
    const warm = new THREE.Color("#ffe7bd");
    const neutral = new THREE.Color("#ffffff");
    const random = seededRandom(2106);

    for (let index = 0; index < ambientCount; index += 1) {
      const radius = 390 + random() * 900;
      const theta = random() * Math.PI * 2;
      const phi = Math.acos(2 * random() - 1);
      positions[index * 3] = radius * Math.sin(phi) * Math.cos(theta);
      positions[index * 3 + 1] = radius * Math.sin(phi) * Math.sin(theta);
      positions[index * 3 + 2] = radius * Math.cos(phi);
      const colorChoice = random();
      const color =
        colorChoice > 0.92 ? warm : colorChoice < 0.08 ? cool : neutral;
      colors[index * 3] = color.r;
      colors[index * 3 + 1] = color.g;
      colors[index * 3 + 2] = color.b;
    }

    for (let index = 0; index < dustCount; index += 1) {
      const pointIndex = ambientCount + index;
      const arm = index % 4;
      const progress = Math.pow(random(), 0.72);
      const radius = 105 + progress * 520;
      const angle =
        arm * (Math.PI / 2) +
        progress * Math.PI * 3.2 +
        (random() - 0.5) * (0.18 + progress * 0.26);
      const spread = (random() - 0.5) * (18 + progress * 44);
      positions[pointIndex * 3] = Math.cos(angle) * radius + spread;
      positions[pointIndex * 3 + 1] = Math.sin(angle) * radius + spread;
      positions[pointIndex * 3 + 2] = (random() - 0.5) * (28 + progress * 70);
      const color = random() > 0.72 ? cool : random() < 0.18 ? warm : neutral;
      colors[pointIndex * 3] = color.r;
      colors[pointIndex * 3 + 1] = color.g;
      colors[pointIndex * 3 + 2] = color.b;
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    const material = new THREE.PointsMaterial({
      size: size.width < 640 ? 1.35 : 1.7,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.72,
      vertexColors: true,
      depthWrite: false,
    });
    const stars = new THREE.Points(geometry, material);
    stars.name = "memory-galaxy-starfield";
    starFieldRef.current = stars;
    scene.add(stars);
    graphRef.current.lights([
      new THREE.AmbientLight("#dce8ff", 2.1),
      new THREE.DirectionalLight("#8ecfff", 1.2),
      new THREE.DirectionalLight("#ffd7a1", 0.7),
    ]);

    return () => {
      scene.remove(stars);
      geometry.dispose();
      material.dispose();
      starFieldRef.current = null;
    };
  }, [size.width]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    const charge = graph.d3Force("charge");
    charge?.strength?.((node: GalaxyNode) => {
      if (node.group === "entity") return -125;
      if (node.group === "category" || node.group === "scope") return -95;
      return -48;
    });
    const link = graph.d3Force("link");
    link?.distance?.((edge: GalaxyLink) =>
      edge.type === "RELATED_TO" ? 72 : edge.type === "IN_CATEGORY" ? 54 : 46,
    );
    link?.strength?.((edge: GalaxyLink) =>
      edge.type === "RELATED_TO" ? 0.24 : 0.38,
    );
    fittedRef.current = false;
    graph.d3ReheatSimulation();
  }, [graphNodes]);

  useEffect(() => {
    const graph = graphRef.current;
    const controls = graph?.controls() as OrbitControlsLike | undefined;
    if (!graph || !controls) return;
    controls.autoRotate = orbiting;
    controls.autoRotateSpeed = 0.34;
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.update?.();

    let animationFrame = 0;
    const renderOrbit = () => {
      controls.update?.();
      graph.refresh();
      animationFrame = window.requestAnimationFrame(renderOrbit);
    };
    if (orbiting) {
      animationFrame = window.requestAnimationFrame(renderOrbit);
    }

    return () => {
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
    };
  }, [orbiting]);

  useEffect(() => {
    graphRef.current?.refresh();
  }, [filter, hoveredId, selectedId, showLinks]);

  const isNodeVisible = useCallback(
    (node: NodeObject<GalaxyNode>) => filter === "all" || node.group === filter,
    [filter],
  );

  const isLinkVisible = useCallback(
    (link: LinkObject<GalaxyNode, GalaxyLink>) => {
      if (!showLinks) return false;
      const source = nodesById.get(
        endpointId(link.source as string | GalaxyNode),
      );
      const target = nodesById.get(
        endpointId(link.target as string | GalaxyNode),
      );
      if (!source || !target) return false;
      return (
        (filter === "all" || source.group === filter) &&
        (filter === "all" || target.group === filter)
      );
    },
    [filter, nodesById, showLinks],
  );

  const focusNode = useCallback((node: GalaxyNode) => {
    setSelectedId(node.id);
    setMobilePanelOpen(true);
    const x = node.x ?? 0;
    const y = node.y ?? 0;
    const z = node.z ?? 0;
    const distance = Math.hypot(x, y, z);
    const ratio = distance > 0 ? 1 + 135 / distance : 1;
    graphRef.current?.cameraPosition(
      distance > 0
        ? { x: x * ratio, y: y * ratio, z: z * ratio + 42 }
        : { x: 0, y: 0, z: 145 },
      { x, y, z },
      900,
    );
  }, []);

  const fitGraph = useCallback(() => {
    setSelectedId(null);
    graphRef.current?.zoomToFit(800, 72, (node) => isNodeVisible(node));
  }, [isNodeVisible]);

  const zoomBy = useCallback(
    (factor: number) => {
      const graph = graphRef.current;
      if (!graph) return;
      const camera = graph.camera();
      const target = selectedNode
        ? new THREE.Vector3(
            selectedNode.x ?? 0,
            selectedNode.y ?? 0,
            selectedNode.z ?? 0,
          )
        : new THREE.Vector3(0, 0, 0);
      const offset = camera.position.clone().sub(target).multiplyScalar(factor);
      const next = target.clone().add(offset);
      graph.cameraPosition(
        { x: next.x, y: next.y, z: next.z },
        { x: target.x, y: target.y, z: target.z },
        280,
      );
    },
    [selectedNode],
  );

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
      className="relative isolate h-[calc(100dvh-190px)] min-h-[480px] max-h-[920px] overflow-hidden rounded-md bg-[#05070a] text-white shadow-[0_18px_45px_rgba(0,0,0,0.22)] sm:h-[calc(100dvh-176px)] sm:min-h-[620px]"
      aria-label={
        language === "zh"
          ? "\u4ea4\u4e92\u5f0f\u8bb0\u5fc6\u661f\u56fe"
          : "Interactive memory galaxy"
      }
      data-testid="memory-galaxy"
    >
      <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_296px]">
        <div ref={containerRef} className="relative min-h-0 overflow-hidden">
          <ForceGraph3D<GalaxyNode, GalaxyLink>
            ref={graphRef}
            width={size.width}
            height={size.height}
            graphData={{ nodes: graphNodes, links: graphLinks }}
            backgroundColor={BACKGROUND}
            showNavInfo={false}
            controlType="orbit"
            nodeId="id"
            nodeLabel={(node) =>
              `<div style="max-width:260px;padding:8px 10px;background:#11151c;border:1px solid rgba(255,255,255,.14);color:#fff;font:12px/1.45 sans-serif"><div style="color:${node.color};margin-bottom:3px">${escapeHtml(copy[node.group])}</div>${escapeHtml(truncate(node.title || node.id))}</div>`
            }
            nodeVisibility={isNodeVisible}
            nodeColor={(node) => {
              if (node.id === selectedId || node.id === hoveredId)
                return "#ffffff";
              if (selectedId && !selectedNeighborIds.has(node.id))
                return "#303740";
              return node.color;
            }}
            nodeVal={(node) => {
              const base =
                1.8 + Math.min(5.5, Math.sqrt(node.degree + 1) * 1.05);
              if (node.id === selectedId) return base * 2.1;
              if (node.id === hoveredId) return base * 1.45;
              return base;
            }}
            nodeRelSize={2.25}
            nodeResolution={10}
            nodeOpacity={0.95}
            linkVisibility={isLinkVisible}
            linkColor={(link) => {
              const sourceId = endpointId(link.source as string | GalaxyNode);
              const targetId = endpointId(link.target as string | GalaxyNode);
              if (
                selectedId &&
                (sourceId === selectedId || targetId === selectedId)
              ) {
                return "rgba(132, 225, 244, 0.88)";
              }
              if (selectedId) return "rgba(105, 116, 135, 0.07)";
              if (link.type === "IN_CATEGORY")
                return "rgba(241, 200, 106, 0.32)";
              if (link.type === "RELATED_TO")
                return "rgba(145, 159, 210, 0.24)";
              return "rgba(119, 159, 171, 0.2)";
            }}
            linkWidth={(link) => {
              const sourceId = endpointId(link.source as string | GalaxyNode);
              const targetId = endpointId(link.target as string | GalaxyNode);
              return selectedId &&
                (sourceId === selectedId || targetId === selectedId)
                ? 1.05
                : 0.18;
            }}
            linkOpacity={0.8}
            linkDirectionalParticles={(link) => {
              const sourceId = endpointId(link.source as string | GalaxyNode);
              const targetId = endpointId(link.target as string | GalaxyNode);
              return selectedId &&
                (sourceId === selectedId || targetId === selectedId)
                ? 2
                : 0;
            }}
            linkDirectionalParticleWidth={1.5}
            linkDirectionalParticleSpeed={0.005}
            linkDirectionalParticleColor={() => "#baf4ff"}
            linkCurvature={(link) => (link.type === "RELATED_TO" ? 0.08 : 0)}
            forceEngine="d3"
            numDimensions={3}
            warmupTicks={80}
            cooldownTicks={220}
            d3VelocityDecay={0.36}
            enableNavigationControls
            enableNodeDrag
            onNodeClick={(node) => focusNode(node)}
            onNodeHover={(node) => setHoveredId(node?.id ?? null)}
            onBackgroundClick={() => setSelectedId(null)}
            onEngineStop={() => {
              if (fittedRef.current) return;
              fittedRef.current = true;
              graphRef.current?.zoomToFit(900, 76);
            }}
          />

          <div className="pointer-events-none absolute inset-x-3 top-3 z-20 flex items-start justify-between gap-3">
            <div className="pointer-events-auto relative min-w-0 flex-1 sm:max-w-[360px]">
              <Search className="pointer-events-none absolute left-3 top-1/2 z-10 size-4 -translate-y-1/2 text-white/45" />
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
                className="h-10 w-full border border-white/15 bg-[#0b0e13]/90 pl-9 pr-9 text-sm text-white outline-none placeholder:text-white/35 focus:border-cyan-300/70 focus:ring-1 focus:ring-cyan-300/50"
              />
              {query ? (
                <button
                  type="button"
                  aria-label={copy.close}
                  onClick={() => setQuery("")}
                  className="absolute right-1 top-1 flex size-8 items-center justify-center text-white/45 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300"
                >
                  <X className="size-4" />
                </button>
              ) : null}
              {query.trim() ? (
                <div className="absolute inset-x-0 top-11 max-h-72 overflow-y-auto border border-white/15 bg-[#0b0e13]/95 shadow-2xl backdrop-blur-md">
                  {searchResults.length ? (
                    searchResults.map((node) => (
                      <button
                        type="button"
                        key={node.id}
                        onClick={() => {
                          focusNode(node);
                          setQuery("");
                        }}
                        className="flex w-full items-center gap-3 border-b border-white/10 px-3 py-2.5 text-left last:border-b-0 hover:bg-white/[0.07] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-300"
                      >
                        <span
                          className="size-2 shrink-0 rounded-full"
                          style={{ backgroundColor: node.color }}
                        />
                        <span className="min-w-0 flex-1 truncate text-xs text-white/85">
                          {node.title || node.id}
                        </span>
                        <span className="text-[10px] text-white/35">
                          {copy[node.group]}
                        </span>
                      </button>
                    ))
                  ) : (
                    <div className="px-3 py-6 text-center text-xs text-white/45">
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
              className="pointer-events-auto flex size-10 shrink-0 items-center justify-center border border-white/15 bg-[#0b0e13]/90 text-white/70 hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300 lg:hidden"
            >
              <PanelRight className="size-4" />
            </button>
          </div>

          <div className="absolute left-3 top-[64px] z-10 flex items-center gap-3 text-[11px] text-white/55 lg:hidden">
            <span className="flex items-center gap-1.5">
              <span className="size-1.5 rounded-full bg-emerald-400" />
              {visibleNodes.length} {copy.nodes}
            </span>
            <span>
              {edges.length} {copy.links}
            </span>
          </div>

          <div
            className={`absolute right-3 top-1/2 z-20 -translate-y-1/2 overflow-hidden border border-white/15 bg-[#0b0e13]/88 shadow-xl backdrop-blur-md ${
              mobilePanelOpen ? "hidden lg:block" : ""
            }`}
          >
            <ControlButton label={copy.zoomIn} onClick={() => zoomBy(0.76)}>
              <ZoomIn className="size-4" />
            </ControlButton>
            <ControlButton label={copy.zoomOut} onClick={() => zoomBy(1.3)}>
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
            <ControlButton
              label={orbiting ? copy.stopOrbit : copy.startOrbit}
              active={orbiting}
              onClick={() => setOrbiting((value) => !value)}
            >
              {orbiting ? (
                <Pause className="size-4" />
              ) : (
                <Orbit className="size-4" />
              )}
            </ControlButton>
          </div>

          <div
            className={`absolute bottom-3 left-3 z-20 max-w-[calc(100%-60px)] items-center gap-2 ${
              mobilePanelOpen ? "hidden lg:flex" : "flex"
            }`}
          >
            <label className="sr-only" htmlFor="galaxy-node-filter">
              {copy.filter}
            </label>
            <select
              id="galaxy-node-filter"
              value={filter}
              onChange={(event) =>
                setFilter(event.target.value as GalaxyFilter)
              }
              className="h-9 max-w-[160px] border border-white/15 bg-[#0b0e13]/90 px-3 text-xs text-white outline-none focus:border-cyan-300/70 focus:ring-1 focus:ring-cyan-300/50"
            >
              {(["all", "memory", "entity", "scope", "category"] as const).map(
                (value) => (
                  <option
                    key={value}
                    value={value}
                    className="bg-[#0b0e13] text-white"
                  >
                    {copy[value]}
                  </option>
                ),
              )}
            </select>
            <div className="hidden items-center gap-3 border border-white/10 bg-[#0b0e13]/72 px-3 py-2 text-[10px] text-white/50 backdrop-blur-sm sm:flex">
              {(["memory", "entity", "scope", "category"] as const).map(
                (group) => (
                  <span key={group} className="flex items-center gap-1.5">
                    <span
                      className="size-1.5 rounded-full"
                      style={{ backgroundColor: GROUP_COLORS[group] }}
                    />
                    {copy[group]}
                  </span>
                ),
              )}
            </div>
          </div>
        </div>

        <aside className="hidden min-h-0 border-l border-white/10 bg-[#0b0e13] lg:flex">
          {renderPanel}
        </aside>
      </div>

      {mobilePanelOpen ? (
        <div className="absolute inset-x-0 bottom-0 z-40 flex h-[62%] min-h-[280px] border-t border-white/15 bg-[#0b0e13] shadow-[0_-18px_45px_rgba(0,0,0,0.45)] sm:h-[58%] sm:min-h-[340px] lg:hidden">
          {!selectedNode ? (
            <button
              type="button"
              aria-label={copy.close}
              onClick={() => setMobilePanelOpen(false)}
              className="absolute right-3 top-3 z-10 flex size-8 items-center justify-center text-white/55 hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300"
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
