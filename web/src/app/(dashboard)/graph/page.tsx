"use client";

import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import {
  Network,
  Search,
  RefreshCw,
  Link2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel, ClassificationStamp } from "@/components/forensic";
import { adminApi } from "@/lib/api";
import { useErrorMessage } from "@/lib/errors";
import { useTenant as useTenantSelection } from "@/context/TenantProvider";
import type {
  ForceGraphMethods,
  LinkObject,
  NodeObject,
} from "react-force-graph-3d";

// Dynamically import 3D Force Graph to avoid SSR issues with canvas/WebGL
type ForceGraphComponent = typeof import("react-force-graph-3d").default;
const ForceGraph3D = dynamic(
  () => import("react-force-graph-3d").then((mod) => mod.default),
  {
    ssr: false,
    loading: () => (
      <div className="h-full w-full flex flex-col items-center justify-center text-center gap-3">
        <LoaderComponent />
      </div>
    )
  }
) as ForceGraphComponent;

function LoaderComponent() {
  const t = useTranslations("graphPage");
  return (
    <>
      <RefreshCw className="h-6 w-6 animate-spin text-signal-amber" />
      <p className="mono-meta">{t("loader")}</p>
    </>
  );
}

interface GraphNodeData {
  id: string;
  type: string;
  description: string;
  classification: string;
  color?: string;
  val?: number;
  x?: number;
  y?: number;
  z?: number;
}

interface GraphLinkData {
  type: string;
  description: string;
  confidence: number;
  classification: string;
}

type GraphNode = NodeObject<GraphNodeData>;
type GraphLink = LinkObject<GraphNodeData, GraphLinkData>;

function endpointId(endpoint: GraphLink["source"]): string {
  if (typeof endpoint === "object" && endpoint !== null) {
    return String(endpoint.id ?? "");
  }
  return String(endpoint ?? "");
}

// Forensic Vault node ramp — restrained bone tints by entity type, never rainbow.
// The focused/selected node is amber; everything else is a bone tint.
const NODE_BONE = "#B8B6AE";
const NODE_BONE_RAMP: Record<string, string> = {
  person: "#E8E6DF",
  organization: "#CFCDC6",
  document: "#B8B6AE",
  location: "#A3A19A",
  event: "#8E8C85",
  concept: "#C4C2BB",
  other: "#8A8880",
};
const NODE_AMBER = "#E8B23A";
const LINK_BASE = "rgba(232,230,223,0.18)";
const LINK_DIM = "rgba(232,230,223,0.04)";
const LINK_ACTIVE = "rgba(232,178,58,0.85)";

export default function GraphPage() {
  const t = useTranslations("graphPage");
  const showError = useErrorMessage();
  const { tenants, tenantId, loadingTenants, tenantError, selectTenant } =
    useTenantSelection();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Data State
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [links, setLinks] = useState<GraphLink[]>([]);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null);

  // Filtering and Search
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedTypes, setSelectedTypes] = useState<string[]>([]);
  const [selectedClassifications, setSelectedClassifications] = useState<string[]>([]);

  // Graph control ref
  const fgRef = useRef<
    ForceGraphMethods<GraphNodeData, GraphLinkData> | undefined
  >(undefined);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 600, height: 500 });

  useEffect(() => {
    if (!containerRef.current) return;
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        setDimensions({
          width: width || 600,
          height: height || 500
        });
      }
    });
    resizeObserver.observe(containerRef.current);
    return () => resizeObserver.disconnect();
  }, []);

  const fetchGraphData = useCallback(async () => {
    if (!tenantId) {
      return;
    }
    setLoading(true);
    setError(null);
    setSelectedNode(null);
    try {
      const res = await adminApi.getGraphNodes(tenantId);
      setNodes(res.nodes);
      setLinks(res.links);

      // Initialize filters with all unique types
      const types = Array.from(new Set(res.nodes.map(n => n.type.toLowerCase())));
      setSelectedTypes(types);
      const classes = Array.from(new Set(res.nodes.map(n => n.classification.toLowerCase())));
      setSelectedClassifications(classes);
    } catch (err: unknown) {
      console.error(err);
      setError(showError(err, t("loadError")));
      // No fake fallback: on error leave the graph empty so the empty-state
      // panel is shown instead of presenting fabricated nodes as real data.
      setNodes([]);
      setLinks([]);
    } finally {
      setLoading(false);
    }
  }, [tenantId, t, showError]);

  useEffect(() => {
    const timer = window.setTimeout(() => void fetchGraphData(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchGraphData]);

  // Filter nodes & links based on UI state — memoized to keep stable references
  // (avoids react-force-graph restarting the simulation on every hover re-render)
  const filteredNodes = useMemo(() => nodes.filter(node => {
    const matchesSearch = node.id.toLowerCase().includes(searchQuery.toLowerCase()) ||
                          node.description.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesType = selectedTypes.includes(node.type.toLowerCase());
    const matchesClass = selectedClassifications.includes(node.classification.toLowerCase());
    return matchesSearch && matchesType && matchesClass;
  }), [nodes, searchQuery, selectedTypes, selectedClassifications]);

  const filteredNodeIds = useMemo(() => new Set(filteredNodes.map(n => n.id)), [filteredNodes]);

  const filteredLinks = useMemo(() => links.filter(link => {
    const srcId = endpointId(link.source);
    const tgtId = endpointId(link.target);
    return filteredNodeIds.has(srcId) && filteredNodeIds.has(tgtId);
  }), [links, filteredNodeIds]);

  // Stable graphData object — only changes when filtered data actually changes
  const graphData = useMemo(() => ({
    nodes: filteredNodes,
    links: filteredLinks
  }), [filteredNodes, filteredLinks]);

  // Unique types and classifications in full dataset
  const uniqueTypes = Array.from(new Set(nodes.map(n => n.type.toLowerCase())));
  const uniqueClassifications = Array.from(new Set(nodes.map(n => n.classification.toLowerCase())));

  const toggleTypeFilter = (type: string) => {
    setSelectedTypes(prev =>
      prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type]
    );
  };

  const toggleClassFilter = (cls: string) => {
    setSelectedClassifications(prev =>
      prev.includes(cls) ? prev.filter(c => c !== cls) : [...prev, cls]
    );
  };

  // Node details helper
  const getNodeNeighbors = (nodeId: string) => {
    return links.filter(l => {
      const srcId = endpointId(l.source);
      const tgtId = endpointId(l.target);
      return srcId === nodeId || tgtId === nodeId;
    });
  };

  // Build adjacency set for the currently hovered node (O(E) once, then O(1) lookups)
  const hoveredNeighborIds = useMemo(() => {
    if (!hoveredNode) return null;
    const set = new Set<string>();
    set.add(hoveredNode.id);
    for (const l of links) {
      const srcId = endpointId(l.source);
      const tgtId = endpointId(l.target);
      if (srcId === hoveredNode.id) set.add(tgtId);
      if (tgtId === hoveredNode.id) set.add(srcId);
    }
    return set;
  }, [hoveredNode, links]);

  // Node color — vault bone ramp by type; selected node is amber. Dim
  // non-neighbours on hover (Obsidian-style focus).
  const getNodeColor = useCallback((node: GraphNode) => {
    if (selectedNode && node.id === selectedNode.id) return NODE_AMBER;
    const t = (node.type || "").toLowerCase();
    const baseColor = NODE_BONE_RAMP[t] || NODE_BONE;
    if (!hoveredNeighborIds) return baseColor; // nothing hovered → full brightness
    if (hoveredNeighborIds.has(node.id)) return baseColor; // neighbour → full brightness
    return `${baseColor}22`; // dim non-neighbours (~13% opacity)
  }, [hoveredNeighborIds, selectedNode]);

  // Link color: highlight edges of hovered node, dim the rest
  const getLinkColor = useCallback((link: GraphLink) => {
    if (!hoveredNeighborIds) return LINK_BASE;
    const srcId = endpointId(link.source);
    const tgtId = endpointId(link.target);
    if (hoveredNeighborIds.has(srcId) && hoveredNeighborIds.has(tgtId)) {
      return LINK_ACTIVE;
    }
    return LINK_DIM;
  }, [hoveredNeighborIds]);

  // Link width: thicken edges connected to hovered node
  const getLinkWidth = useCallback((link: GraphLink) => {
    if (!hoveredNeighborIds) return 1.5;
    const srcId = endpointId(link.source);
    const tgtId = endpointId(link.target);
    if (hoveredNeighborIds.has(srcId) && hoveredNeighborIds.has(tgtId)) return 3;
    return 0.5;
  }, [hoveredNeighborIds]);

  // Hover handler
  const handleNodeHover = useCallback((node: GraphNode | null) => {
    setHoveredNode(node || null);
    // Change cursor style
    if (containerRef.current) {
      containerRef.current.style.cursor = node ? "pointer" : "grab";
    }
  }, []);

  // Center node in viewport
  const handleFocusNode = (node: GraphNode) => {
    if (!fgRef.current || !node) return;

    // Search the node object in force-graph model
    const graphNode = filteredNodes.find(n => n.id === node.id);
    if (!graphNode) return;

    // Center camera on node coordinates
    const distance = 40;
    const distRatio = 1 + distance / Math.hypot(graphNode.x || 0, graphNode.y || 0, graphNode.z || 0);

    fgRef.current.cameraPosition(
      {
        x: (graphNode.x || 0) * distRatio,
        y: (graphNode.y || 0) * distRatio,
        z: (graphNode.z || 0) * distRatio
      },
      {
        x: graphNode.x || 0,
        y: graphNode.y || 0,
        z: graphNode.z || 0,
      },
      2500 // transition ms
    );
    setSelectedNode(node);
  };

  // Mono toggle chip — active = amber text + amber border, inactive = bone-dim hairline
  const chipClass = (active: boolean) =>
    `font-mono text-[0.625rem] uppercase tracking-wider px-2 py-1 border transition-colors cursor-pointer ${
      active
        ? "text-signal-amber border-signal-amber"
        : "text-bone-dim border-hairline hover:text-bone"
    }`;

  return (
    <div className="max-w-7xl mx-auto h-[calc(100vh-140px)] flex flex-col space-y-6">
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-4 border-b border-hairline flex-shrink-0">
        <div>
          <p className="mono-meta">{t("eyebrow")}</p>
          <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-bone">
            {t("title")}
          </h1>
        </div>

        <div className="flex items-center gap-3">
          <select
            value={tenantId ?? ""}
            onChange={(e) => {
              selectTenant(e.target.value);
            }}
            disabled={loadingTenants || tenants.length === 0}
            className="bg-vault-raised border border-hairline px-2.5 py-1.5 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
          >
            {!tenantId && <option value="">{t("noTenant")}</option>}
            {tenants.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>
                {tenant.display_name}
              </option>
            ))}
          </select>
          <Button size="sm" onClick={fetchGraphData} variant="outline" disabled={loading} className="gap-1.5">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            {t("refresh")}
          </Button>
        </div>
      </header>

      {error && (
        <div className="border border-seal-red/40 bg-vault-sunk px-4 py-3 max-w-xl flex-shrink-0">
          <p className="mono-meta text-seal-red mb-1">{t("error")}</p>
          <p className="font-mono text-[0.625rem] text-bone-dim">{error}</p>
          <p className="font-mono text-[0.625rem] text-bone-dim mt-1">{t("emptyOnError")}</p>
        </div>
      )}
      {tenantError && (
        <div className="border border-seal-red/40 bg-vault-sunk px-4 py-3 max-w-xl flex-shrink-0">
          <p className="mono-meta text-seal-red mb-1">{t("tenantError")}</p>
          <p className="font-mono text-[0.625rem] text-bone-dim">{tenantError}</p>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 items-stretch flex-1 min-h-0">
        <div className="flex flex-col gap-6 h-full overflow-y-auto custom-scrollbar flex-shrink-0">
          <Panel label={t("find")} className="flex-shrink-0">
            <div className="px-4 py-4 space-y-3">
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-bone-dim" />
                <Input
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t("searchPlaceholder")}
                  aria-label={t("find")}
                  className="pl-9 bg-vault-raised border-hairline font-mono text-xs"
                />
              </div>

              {searchQuery && (
                <div className="bg-vault-sunk border border-hairline max-h-40 overflow-y-auto custom-scrollbar divide-y divide-hairline font-mono text-[0.6875rem]">
                  {filteredNodes.slice(0, 5).map((node) => (
                    <button
                      key={node.id}
                      onClick={() => handleFocusNode(node)}
                      className="w-full text-left px-2.5 py-2 text-bone-dim hover:text-signal-amber hover:bg-[rgba(232,230,223,0.03)] transition-colors truncate block"
                    >
                      {node.id}{" "}
                      <span className="text-bone-dim">({node.type})</span>
                    </button>
                  ))}
                  {filteredNodes.length === 0 && (
                    <span className="px-2.5 py-2 mono-meta block text-center">{t("noMatch")}</span>
                  )}
                </div>
              )}
            </div>
          </Panel>

          <Panel label={t("filters")} className="flex-1">
            <div className="px-4 py-4 space-y-5">
              {/* Entity types */}
              <div className="space-y-2">
                <p className="mono-meta">{t("entityTypes")}</p>
                <div className="flex flex-wrap gap-1.5 max-h-48 overflow-y-auto custom-scrollbar">
                  {uniqueTypes.map((type) => (
                    <button
                      key={type}
                      type="button"
                      onClick={() => toggleTypeFilter(type)}
                      className={chipClass(selectedTypes.includes(type))}
                    >
                      {type}
                    </button>
                  ))}
                </div>
              </div>

              {/* Classification levels */}
              <div className="space-y-2">
                <p className="mono-meta">{t("classification")}</p>
                <div className="flex flex-wrap gap-1.5">
                  {uniqueClassifications.map((cls) => (
                    <button
                      key={cls}
                      type="button"
                      onClick={() => toggleClassFilter(cls)}
                      className={chipClass(selectedClassifications.includes(cls))}
                    >
                      {cls}
                    </button>
                  ))}
                </div>
              </div>

              {/* Counts */}
              <dl className="border-t border-hairline pt-3 space-y-1.5 font-mono text-xs">
                <div className="flex justify-between">
                  <dt className="mono-meta normal-case">{t("filteredNodes")}</dt>
                  <dd className="text-bone tabular-nums">{filteredNodes.length}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="mono-meta normal-case">{t("filteredLinks")}</dt>
                  <dd className="text-bone tabular-nums">{filteredLinks.length}</dd>
                </div>
              </dl>
            </div>
          </Panel>
        </div>

        <Panel label={t("viewport")} className="lg:col-span-2 overflow-hidden relative flex flex-col min-h-[500px]">
          <div ref={containerRef} className="flex-1 w-full h-full bg-vault-sunk relative min-h-[500px]">
            {filteredNodes.length === 0 && !loading && (
              <div className="absolute inset-0 flex flex-col items-center justify-center text-center p-6 bg-vault-sunk z-10 gap-4">
                <Network className="h-8 w-8 text-bone-dim" />
                <div className="space-y-1.5 max-w-md">
                  <p className="mono-meta text-bone">{t("emptyTitle")}</p>
                  <p className="text-xs text-bone-dim leading-relaxed">
                    {t("emptyCopy", { tenantId: tenantId ?? "—" })}
                  </p>
                </div>
              </div>
            )}
            <ForceGraph3D<GraphNodeData, GraphLinkData>
              ref={fgRef}
              graphData={graphData}
              width={dimensions.width}
              height={dimensions.height}
              nodeLabel={(node: GraphNode) => `${node.id} (${node.type})`}
              nodeColor={getNodeColor}
              nodeVal={4}
              nodeOpacity={1}
              linkWidth={getLinkWidth}
              linkColor={getLinkColor}
              linkDirectionalArrowLength={3.5}
              linkDirectionalArrowRelPos={1}
              onNodeClick={handleFocusNode}
              onNodeHover={handleNodeHover}
              enableNodeDrag={false}
              cooldownTicks={100}
              d3AlphaDecay={0.05}
              d3VelocityDecay={0.3}
              controlType="trackball"
              backgroundColor="rgba(0,0,0,0)"
            />
          </div>
        </Panel>

        <Panel
          label={t("inspector")}
          classification={selectedNode ? selectedNode.classification : undefined}
          className="flex flex-col"
        >
          <div className="flex-1 px-4 py-4 overflow-y-auto custom-scrollbar">
            {selectedNode ? (
              <div className="space-y-4 font-mono text-xs">
                <div>
                  <p className="mono-meta">{t("entityId")}</p>
                  <p className="text-bone mt-1 leading-normal break-words">{selectedNode.id}</p>
                </div>

                <div>
                  <p className="mono-meta">{t("entityType")}</p>
                  <p className="text-signal-amber mt-1 uppercase tracking-wider">
                    {selectedNode.type}
                  </p>
                </div>

                <div>
                  <p className="mono-meta">{t("classification")}</p>
                  <div className="mt-1.5">
                    <ClassificationStamp level={selectedNode.classification} />
                  </div>
                </div>

                <div>
                  <p className="mono-meta">{t("description")}</p>
                  <div className="bg-vault-sunk border border-hairline px-2.5 py-2 text-[0.625rem] text-bone-dim leading-relaxed mt-1.5">
                    {selectedNode.description || t("noDescription")}
                  </div>
                </div>

                <div>
                  <div className="flex items-center gap-1.5 mono-meta border-b border-hairline pb-1.5 mb-2">
                    <Link2 className="h-3 w-3" />{" "}
                    {t("adjacentEdges", {
                      count: getNodeNeighbors(selectedNode.id).length,
                    })}
                  </div>
                  <ul className="space-y-1.5 max-h-40 overflow-y-auto custom-scrollbar">
                    {getNodeNeighbors(selectedNode.id).map((edge, idx) => {
                      const srcId = typeof edge.source === "object" ? edge.source.id : edge.source;
                      const tgtId = typeof edge.target === "object" ? edge.target.id : edge.target;
                      const neighborId = srcId === selectedNode.id ? tgtId : srcId;
                      const relationDirection = srcId === selectedNode.id ? "→" : "←";
                      const neighborNode = nodes.find(n => n.id === neighborId);
                      return (
                        <li
                          key={idx}
                          onClick={() => neighborNode && handleFocusNode(neighborNode)}
                          className="px-2.5 py-2 bg-vault-sunk border border-hairline hover:border-signal-amber/40 flex items-center justify-between gap-2 cursor-pointer transition-colors"
                        >
                          <div className="min-w-0">
                            <span className="mono-meta normal-case block">
                              {relationDirection} {edge.type}
                            </span>
                            <span className="text-bone text-[0.625rem] truncate block">
                              {neighborId}
                            </span>
                          </div>
                          <span className="text-bone-dim text-[0.625rem] tabular-nums">
                            {edge.confidence.toFixed(2)}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              </div>
            ) : (
              <div className="h-full flex flex-col items-center justify-center text-center gap-2 py-10">
                <Network className="h-6 w-6 text-bone-dim" />
                <span className="mono-meta normal-case">
                  {t("selectNode")}
                </span>
              </div>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}
