"use client";

import React, { useState, useEffect, useRef } from "react";
import dynamic from "next/dynamic";
import { 
  Network, 
  Search, 
  Settings, 
  Filter, 
  RefreshCw, 
  Info, 
  Eye, 
  ShieldAlert, 
  Compass,
  Cpu,
  Link2,
  Tag
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { adminApi } from "@/lib/api";

// Dynamically import 3D Force Graph to avoid SSR issues with canvas/WebGL
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
);

function LoaderComponent() {
  return (
    <>
      <div className="h-8 w-8 animate-spin rounded-full border-4 border-accent-indigo border-t-transparent"></div>
      <p className="text-xs font-mono text-foreground/50 tracking-wider">INITIALIZING WEBGL 3D VIEWPORT...</p>
    </>
  );
}

interface GraphNode {
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

interface GraphLink {
  source: string | any;
  target: string | any;
  type: string;
  description: string;
  confidence: number;
  classification: string;
}

// Premium entity type color mapping
const ENTITY_COLORS: Record<string, string> = {
  person: "#34d399",       // Emerald
  organization: "#22d3ee", // Cyan
  document: "#818cf8",     // Indigo
  location: "#fbbf24",     // Amber
  event: "#a78bfa",        // Violet
  concept: "#f472b6",      // Pink
  other: "#94a3b8"         // Slate
};

const CLASSIFICATION_COLORS: Record<string, string> = {
  public: "text-accent-emerald bg-accent-emerald/10 border-accent-emerald/20",
  internal: "text-accent-indigo bg-accent-indigo/10 border-accent-indigo/20",
  restricted: "text-accent-rose bg-accent-rose/10 border-accent-rose/20"
};

export default function GraphPage() {
  const [tenantId, setTenantId] = useState("tenant_a");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  // Data State
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [links, setLinks] = useState<GraphLink[]>([]);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  
  // Filtering and Search
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedTypes, setSelectedTypes] = useState<string[]>([]);
  const [selectedClassifications, setSelectedClassifications] = useState<string[]>([]);
  
  // Graph control ref
  const fgRef = useRef<any>(null);

  useEffect(() => {
    const storedTenant = localStorage.getItem("securgraph_tenant");
    if (storedTenant) {
      setTenantId(storedTenant);
    }
  }, []);

  useEffect(() => {
    fetchGraphData();
  }, [tenantId]);

  const fetchGraphData = async () => {
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
    } catch (err: any) {
      console.error(err);
      setError(err?.message || "Failed to load graph nodes from Neo4j.");
      mockFallbackGraph();
    } finally {
      setLoading(false);
    }
  };

  const mockFallbackGraph = () => {
    // Generate beautiful demo data if neo4j/api is unavailable
    const demoNodes: GraphNode[] = [
      { id: "SecurGraph RAG", type: "concept", description: "Consensus-based Hybrid RAG", classification: "public" },
      { id: "Stefano Hernandez", type: "person", description: "Lead Engineer on SecureGraph", classification: "internal" },
      { id: "Google DeepMind", type: "organization", description: "AI Research Laboratory", classification: "public" },
      { id: "OpenFGA Server", type: "document", description: "Fine-grained access control database", classification: "restricted" },
      { id: "Neo4j Database", type: "document", description: "Property Graph Database for structure", classification: "internal" },
      { id: "Qdrant Vector DB", type: "document", description: "Dense semantic matching retriever", classification: "internal" },
      { id: "granite4.1:3b", type: "concept", description: "Synthesis Model", classification: "public" }
    ];

    const demoLinks: GraphLink[] = [
      { source: "Stefano Hernandez", target: "SecurGraph RAG", type: "CREATED", description: "Stefano engineered the SecurGraph framework", confidence: 1.0, classification: "internal" },
      { source: "Google DeepMind", target: "Stefano Hernandez", type: "EMPLOYED", description: "Stefano works at DeepMind", confidence: 0.95, classification: "public" },
      { source: "SecurGraph RAG", target: "OpenFGA Server", type: "USES", description: "Uses OpenFGA for ReBAC verification", confidence: 1.0, classification: "public" },
      { source: "SecurGraph RAG", target: "Neo4j Database", type: "USES", description: "Graph retrieval lane", confidence: 1.0, classification: "public" },
      { source: "SecurGraph RAG", target: "Qdrant Vector DB", type: "USES", description: "Vector retrieval lane", confidence: 1.0, classification: "public" },
      { source: "SecurGraph RAG", target: "granite4.1:3b", type: "RUNS_ON", description: "Uses granite for synthesis", confidence: 0.85, classification: "public" }
    ];

    setNodes(demoNodes);
    setLinks(demoLinks);
    setSelectedTypes(["concept", "person", "organization", "document"]);
    setSelectedClassifications(["public", "internal", "restricted"]);
  };

  // Filter nodes & links based on UI state
  const filteredNodes = nodes.filter(node => {
    const matchesSearch = node.id.toLowerCase().includes(searchQuery.toLowerCase()) || 
                          node.description.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesType = selectedTypes.includes(node.type.toLowerCase());
    const matchesClass = selectedClassifications.includes(node.classification.toLowerCase());
    return matchesSearch && matchesType && matchesClass;
  });

  const filteredNodeIds = new Set(filteredNodes.map(n => n.id));

  const filteredLinks = links.filter(link => {
    const srcId = typeof link.source === "object" ? link.source.id : link.source;
    const tgtId = typeof link.target === "object" ? link.target.id : link.target;
    return filteredNodeIds.has(srcId) && filteredNodeIds.has(tgtId);
  });

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
      const srcId = typeof l.source === "object" ? l.source.id : l.source;
      const tgtId = typeof l.target === "object" ? l.target.id : l.target;
      return srcId === nodeId || tgtId === nodeId;
    });
  };

  // Color and sizing calculations
  const getNodeColor = (node: any) => {
    const t = (node.type || "").toLowerCase();
    return ENTITY_COLORS[t] || ENTITY_COLORS.other;
  };

  // Center node in viewport
  const handleFocusNode = (node: any) => {
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
      graphNode, 
      2500 // transition ms
    );
    setSelectedNode(node);
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto h-[calc(100vh-140px)] flex flex-col">
      
      {/* Top Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border/20 pb-4 flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 rounded-xl bg-accent-indigo/15 border border-accent-indigo/30 flex items-center justify-center text-accent-indigo">
            <Compass className="h-4.5 w-4.5" />
          </div>
          <div>
            <h1 className="text-xl md:text-2xl font-black text-foreground">
              3D Knowledge Graph Explorer
            </h1>
            <p className="text-[10px] text-foreground/45 font-mono">
              Interactive structural exploration of Neo4j entities.
            </p>
          </div>
        </div>
        
        <div className="flex items-center gap-3">
          <span className="text-xs text-foreground/50 font-mono">Scope Tenant:</span>
          <select
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
            className="bg-black/45 border border-border/40 rounded-lg px-2.5 py-1.5 text-xs text-foreground font-mono focus:outline-none focus:border-accent-indigo"
          >
            <option value="tenant_a">tenant_a (Default)</option>
            <option value="mrd">mrd</option>
            <option value="tenant_b">tenant_b</option>
          </select>
          <Button
            size="sm"
            onClick={fetchGraphData}
            variant="outline"
            className="border-border/40 hover:bg-white/5 cursor-pointer h-8 text-xs font-bold"
            disabled={loading}
          >
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? "animate-spin" : ""}`} />
            Sync Neo4j
          </Button>
        </div>
      </div>

      {error && (
        <div className="p-3 bg-accent-rose/10 border border-accent-rose/25 text-accent-rose rounded-lg text-xs flex items-start gap-2 max-w-xl flex-shrink-0">
          <ShieldAlert className="h-4.5 w-4.5 flex-shrink-0 mt-0.5" />
          <div className="space-y-1 font-mono text-[10px]">
            <span className="font-bold font-sans text-xs">Connessione Neo4j fallita</span>
            <p>{error}</p>
            <p className="text-foreground/50 font-sans">Caricamento del grafo dimostrativo locale.</p>
          </div>
        </div>
      )}

      {/* Main Grid Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 items-stretch flex-1 min-h-0">
        
        {/* Left pane: Filter and Search (1 col) */}
        <div className="flex flex-col gap-6 h-full overflow-y-auto custom-scrollbar flex-shrink-0">
          
          {/* Search Card */}
          <Card className="glassmorphism border-border/50 text-foreground flex-shrink-0">
            <CardHeader className="pb-3 border-b border-border/10">
              <CardTitle className="text-sm font-bold flex items-center gap-1.5">
                <Search className="h-4.5 w-4.5 text-accent-indigo" />
                Find Entity
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 space-y-3">
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-foreground/40" />
                <Input
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Nome o descrizione..."
                  className="pl-9 bg-black/45 border-border/40 text-xs focus-visible:ring-accent-indigo/50 h-9"
                />
              </div>
              
              {/* Search Suggestions */}
              {searchQuery && (
                <div className="bg-black/35 border border-border/20 rounded-lg max-h-40 overflow-y-auto custom-scrollbar p-1 text-[11px] font-mono divide-y divide-border/10">
                  {filteredNodes.slice(0, 5).map((node) => (
                    <button
                      key={node.id}
                      onClick={() => handleFocusNode(node)}
                      className="w-full text-left p-2 hover:bg-white/5 hover:text-accent-indigo rounded transition truncate block"
                    >
                      {node.id} <span className="text-foreground/30 text-[9px]">({node.type})</span>
                    </button>
                  ))}
                  {filteredNodes.length === 0 && (
                    <span className="p-2 text-foreground/40 block text-center">Nessun match trovato.</span>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Filter Configuration Card */}
          <Card className="glassmorphism border-border/50 text-foreground flex-1">
            <CardHeader className="pb-3 border-b border-border/10">
              <CardTitle className="text-sm font-bold flex items-center gap-1.5">
                <Filter className="h-4.5 w-4.5 text-accent-cyan" />
                Graph Filters
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 space-y-5 text-xs">
              
              {/* Entity Types */}
              <div className="space-y-2">
                <label className="font-mono text-[10px] text-foreground/50 uppercase tracking-wider block">
                  Entity Types
                </label>
                <div className="space-y-1.5 max-h-48 overflow-y-auto custom-scrollbar pr-1">
                  {uniqueTypes.map((type) => (
                    <label key={type} className="flex items-center gap-2 cursor-pointer text-foreground/80 hover:text-foreground">
                      <input
                        type="checkbox"
                        checked={selectedTypes.includes(type)}
                        onChange={() => toggleTypeFilter(type)}
                        className="rounded border-border/50 text-accent-cyan focus:ring-0 cursor-pointer h-3.5 w-3.5 bg-black/45"
                      />
                      <span className="font-mono text-[11px] capitalize flex items-center gap-1.5">
                        <span className="h-2 w-2 rounded-full" style={{ backgroundColor: ENTITY_COLORS[type] || ENTITY_COLORS.other }}></span>
                        {type}
                      </span>
                    </label>
                  ))}
                </div>
              </div>

              {/* Classification Levels */}
              <div className="space-y-2">
                <label className="font-mono text-[10px] text-foreground/50 uppercase tracking-wider block">
                  Classification
                </label>
                <div className="space-y-1.5">
                  {uniqueClassifications.map((cls) => (
                    <label key={cls} className="flex items-center gap-2 cursor-pointer text-foreground/80 hover:text-foreground">
                      <input
                        type="checkbox"
                        checked={selectedClassifications.includes(cls)}
                        onChange={() => toggleClassFilter(cls)}
                        className="rounded border-border/50 text-accent-cyan focus:ring-0 cursor-pointer h-3.5 w-3.5 bg-black/45"
                      />
                      <span className="font-mono text-[11px] uppercase">{cls}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="text-[10px] text-foreground/45 border-t border-border/10 pt-3 leading-normal space-y-1">
                <div className="flex justify-between">
                  <span>Filtered Nodes:</span>
                  <span className="font-mono text-foreground font-bold">{filteredNodes.length}</span>
                </div>
                <div className="flex justify-between">
                  <span>Filtered Links:</span>
                  <span className="font-mono text-foreground font-bold">{filteredLinks.length}</span>
                </div>
              </div>

            </CardContent>
          </Card>

        </div>

        {/* Center pane: 3D Force Graph (2 cols) */}
        <Card className="lg:col-span-2 glassmorphism border-border/50 text-foreground overflow-hidden relative flex flex-col">
          <div className="flex-1 bg-[#0a0a14]/65 relative">
            <ForceGraph3D
              ref={fgRef}
              graphData={{ nodes: filteredNodes, links: filteredLinks }}
              nodeLabel={(n: any) => `${n.id} (${n.type})`}
              nodeColor={getNodeColor}
              nodeVal={4}
              linkWidth={1.5}
              linkColor={() => "rgba(255, 255, 255, 0.12)"}
              linkDirectionalArrowLength={3.5}
              linkDirectionalArrowRelPos={1}
              onNodeClick={handleFocusNode}
              controlType="trackball"
              backgroundColor="#0a0a14"
            />
          </div>
        </Card>

        {/* Right pane: Node Inspector Slide-out (1 col) */}
        <Card className="glassmorphism border-border/50 text-foreground flex flex-col justify-between">
          <CardHeader className="pb-3 border-b border-border/10 flex-shrink-0">
            <CardTitle className="text-sm font-bold flex items-center gap-1.5">
              <Cpu className="h-4.5 w-4.5 text-accent-indigo" />
              Entity Inspector
            </CardTitle>
            <CardDescription className="text-[10px]">
              Detailed properties of selected Neo4j node.
            </CardDescription>
          </CardHeader>
          
          <CardContent className="flex-1 p-4 overflow-y-auto custom-scrollbar text-xs font-mono space-y-4">
            {selectedNode ? (
              <div className="space-y-4">
                <div>
                  <span className="text-[9px] text-foreground/40 uppercase block">Name / Entity ID</span>
                  <p className="text-foreground/90 font-sans mt-0.5 text-xs font-bold leading-normal">{selectedNode.id}</p>
                </div>

                <div>
                  <span className="text-[9px] text-foreground/40 uppercase block">Entity Type</span>
                  <Badge className="bg-accent-indigo/15 text-accent-indigo border-accent-indigo/25 mt-1 h-5 text-[9px] font-bold capitalize">
                    {selectedNode.type}
                  </Badge>
                </div>

                <div>
                  <span className="text-[9px] text-foreground/40 uppercase block">Classification Level</span>
                  <Badge variant="outline" className={`mt-1 font-bold uppercase ${
                    CLASSIFICATION_COLORS[selectedNode.classification.toLowerCase()] || "text-foreground border-border/30"
                  }`}>
                    {selectedNode.classification}
                  </Badge>
                </div>

                <div>
                  <span className="text-[9px] text-foreground/40 uppercase block">Description</span>
                  <div className="bg-black/35 rounded-lg border border-border/15 p-2.5 text-[10px] text-foreground/80 font-sans leading-relaxed mt-1">
                    {selectedNode.description || "Nessuna descrizione presente nel database."}
                  </div>
                </div>

                {/* Neighbors List */}
                <div>
                  <div className="flex items-center gap-1 text-[9px] text-foreground/40 uppercase border-b border-border/10 pb-1.5 mb-2">
                    <Link2 className="h-3.5 w-3.5" /> Adjacent Edges ({getNodeNeighbors(selectedNode.id).length})
                  </div>
                  <div className="space-y-2 max-h-40 overflow-y-auto custom-scrollbar pr-1">
                    {getNodeNeighbors(selectedNode.id).map((edge, idx) => {
                      const srcId = typeof edge.source === "object" ? edge.source.id : edge.source;
                      const tgtId = typeof edge.target === "object" ? edge.target.id : edge.target;
                      const neighborId = srcId === selectedNode.id ? tgtId : srcId;
                      const relationDirection = srcId === selectedNode.id ? "→" : "←";
                      const neighborNode = nodes.find(n => n.id === neighborId);
                      return (
                        <div
                          key={idx}
                          onClick={() => neighborNode && handleFocusNode(neighborNode)}
                          className="p-2 bg-black/20 hover:bg-white/5 border border-border/10 hover:border-accent-indigo/30 rounded-lg flex items-center justify-between cursor-pointer transition"
                        >
                          <div className="min-w-0 pr-2">
                            <span className="text-[9px] text-foreground/35 block font-mono">
                              {relationDirection} {edge.type}
                            </span>
                            <span className="font-sans font-bold text-foreground/80 text-[10px] truncate block">
                              {neighborId}
                            </span>
                          </div>
                          <Badge className="bg-accent-cyan/10 text-accent-cyan border-accent-cyan/20 h-4 text-[8px]">
                            {edge.confidence.toFixed(2)}
                          </Badge>
                        </div>
                      );
                    })}
                  </div>
                </div>

              </div>
            ) : (
              <div className="h-full flex flex-col items-center justify-center text-center text-foreground/40 gap-2 py-10">
                <Network className="h-6 w-6 text-foreground/20 animate-pulse" />
                <span className="text-[10px] font-sans">Clicca su una entità del grafo 3D per ispezionarne le relazioni e i metadati.</span>
              </div>
            )}
          </CardContent>
        </Card>

      </div>

    </div>
  );
}
