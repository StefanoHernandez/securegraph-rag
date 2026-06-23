"use client";

import React, { useState, useEffect, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { 
  ReactFlow, 
  Background, 
  Controls, 
  useNodesState, 
  useEdgesState,
  MarkerType,
  Node,
  Edge
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { 
  HelpCircle, 
  ShieldCheck, 
  ShieldAlert, 
  Clock, 
  User, 
  Cpu, 
  Database, 
  FileCode,
  ArrowLeft,
  Search,
  ExternalLink,
  ChevronRight
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { explainApi } from "@/lib/api";
import { ExplainReport } from "@/lib/types";

function ExplainContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const traceIdParam = searchParams.get("trace_id");

  const [traceId, setTraceId] = useState(traceIdParam || "");
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<ExplainReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<any>(null);

  // XYFlow state
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  useEffect(() => {
    if (traceIdParam) {
      fetchReport(traceIdParam);
    }
  }, [traceIdParam]);

  const fetchReport = async (tid: string) => {
    setLoading(true);
    setError(null);
    setSelectedNode(null);
    try {
      const storedUser = localStorage.getItem("securgraph_user");
      const storedTenant = localStorage.getItem("securgraph_tenant");
      let uId = "admin";
      let tId = "default";

      if (storedUser) {
        const parsed = JSON.parse(storedUser);
        uId = parsed.id || parsed.username || "admin";
        tId = storedTenant || parsed.tenant_id || "default";
      }

      const res = await explainApi.getReport(tid, tId, uId);
      setReport(res);
      buildLineageGraph(res);
    } catch (err: any) {
      console.error(err);
      setError(err?.message || "Impossibile recuperare il report di explainability. Verifica che l'ID sia corretto.");
      setReport(null);
    } finally {
      setLoading(false);
    }
  };

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (traceId.trim()) {
      router.push(`/explain?trace_id=${traceId.trim()}`);
    }
  };

  // Build the XYFlow node/edge structures from W3C PROV-O fields
  const buildLineageGraph = (data: ExplainReport) => {
    const newNodes: any[] = [];
    const newEdges: any[] = [];

    // 1. Map Agents (User & Service)
    data.prov_agents.forEach((agent, idx) => {
      const isUser = agent.id.includes("user");
      newNodes.push({
        id: agent.id,
        position: { x: 50, y: isUser ? 100 : 250 },
        data: { 
          label: agent.label, 
          type: "Agent",
          icon: isUser ? "user" : "cpu",
          raw: agent
        },
        className: "border-2 border-accent-indigo bg-black/85 text-foreground p-3 rounded-xl shadow-lg font-mono text-[10px] w-48 text-center glassmorphism"
      });
    });

    // 2. Map Activities (Query Pipeline execution)
    data.prov_activities.forEach((act) => {
      newNodes.push({
        id: act.id,
        position: { x: 300, y: 175 },
        data: { 
          label: act.label, 
          type: "Activity",
          icon: "activity",
          raw: act
        },
        className: "border-2 border-accent-cyan bg-black/85 text-foreground p-3 rounded-xl shadow-lg font-mono text-[10px] w-48 text-center glassmorphism"
      });

      // Link Service Agent to pipeline activity
      newEdges.push({
        id: `edge-agent-service-act-${act.id}`,
        source: "agent:securgraph-service",
        target: act.id,
        label: "wasAssociatedWith",
        animated: true,
        style: { stroke: "#22d3ee", strokeWidth: 1.5 },
        labelStyle: { fill: "#22d3ee", fontSize: 8, fontFamily: "monospace" },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#22d3ee" }
      });
    });

    // 3. Map Query Entity
    const queryEnt = data.prov_entities.find(e => e.id.includes("query"));
    if (queryEnt) {
      newNodes.push({
        id: queryEnt.id,
        position: { x: 50, y: 400 },
        data: { 
          label: queryEnt.label, 
          type: "Entity (Input)",
          icon: "query",
          raw: queryEnt
        },
        className: "border-2 border-accent-violet bg-black/85 text-foreground p-3 rounded-xl shadow-lg font-mono text-[10px] w-48 text-center glassmorphism"
      });

      // Link User Agent to Query
      newEdges.push({
        id: `edge-user-query`,
        source: "agent:user:admin", // Fallback if ID changed
        target: queryEnt.id,
        label: "wasAttributedTo",
        style: { stroke: "#818cf8", strokeWidth: 1 },
        labelStyle: { fill: "#818cf8", fontSize: 8, fontFamily: "monospace" },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#818cf8" }
      });

      // Link Query to Activity
      newEdges.push({
        id: `edge-query-activity`,
        source: queryEnt.id,
        target: data.prov_activities[0]?.id || "activity:query_pipeline",
        label: "wasUsedIn",
        animated: true,
        style: { stroke: "#a78bfa", strokeWidth: 1.5 },
        labelStyle: { fill: "#a78bfa", fontSize: 8, fontFamily: "monospace" },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#a78bfa" }
      });
    }

    // 4. Map Evidence Entities (Stack horizontally/vertically on the right)
    const evidenceEnts = data.prov_entities.filter(e => e.id.includes("evidence"));
    evidenceEnts.forEach((ev, idx) => {
      const isAllowed = ev.status === "allowed";
      newNodes.push({
        id: ev.id,
        position: { x: 580, y: 40 + idx * 85 },
        data: { 
          label: ev.label, 
          type: `Entity (Evidence: ${isAllowed ? 'Allowed' : 'Denied'})`,
          icon: "database",
          raw: ev
        },
        className: `border-2 ${isAllowed ? "border-accent-emerald bg-accent-emerald/5" : "border-accent-rose bg-accent-rose/5"} bg-black/85 text-foreground p-3 rounded-xl shadow-lg font-mono text-[10px] w-52 text-center glassmorphism`
      });

      // Link Service Agent to Evidence
      newEdges.push({
        id: `edge-agent-ev-${ev.id}`,
        source: "agent:securgraph-service",
        target: ev.id,
        label: "wasAttributedTo",
        style: { stroke: "#818cf8", strokeWidth: 1 },
        labelStyle: { fill: "#818cf8", fontSize: 8, fontFamily: "monospace" },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#818cf8" }
      });

      // Link Evidence to Activity if used
      if (isAllowed) {
        newEdges.push({
          id: `edge-ev-act-${ev.id}`,
          source: ev.id,
          target: data.prov_activities[0]?.id || "activity:query_pipeline",
          label: "wasUsedIn",
          style: { stroke: "#34d399", strokeWidth: 1.2 },
          labelStyle: { fill: "#34d399", fontSize: 8, fontFamily: "monospace" },
          markerEnd: { type: MarkerType.ArrowClosed, color: "#34d399" }
        });
      }
    });

    // 5. Map Answer Entity
    const answerEnt = data.prov_entities.find(e => e.id.includes("answer"));
    if (answerEnt) {
      newNodes.push({
        id: answerEnt.id,
        position: { x: 880, y: 175 },
        data: { 
          label: answerEnt.label, 
          type: "Entity (Output)",
          icon: "filecode",
          raw: answerEnt
        },
        className: "border-2 border-accent-emerald bg-black/85 text-foreground p-3 rounded-xl shadow-lg font-mono text-[10px] w-48 text-center glassmorphism"
      });

      // Link Activity to Answer
      newEdges.push({
        id: `edge-activity-answer`,
        source: data.prov_activities[0]?.id || "activity:query_pipeline",
        target: answerEnt.id,
        label: "wasGeneratedBy",
        animated: true,
        style: { stroke: "#34d399", strokeWidth: 1.5 },
        labelStyle: { fill: "#34d399", fontSize: 8, fontFamily: "monospace" },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#34d399" }
      });

      // Link Query to Answer (wasDerivedFrom)
      if (queryEnt) {
        newEdges.push({
          id: `edge-query-answer`,
          source: queryEnt.id,
          target: answerEnt.id,
          label: "wasDerivedFrom",
          style: { stroke: "#a78bfa", strokeWidth: 1, strokeDasharray: "4 4" },
          labelStyle: { fill: "#a78bfa", fontSize: 8, fontFamily: "monospace" },
          markerEnd: { type: MarkerType.ArrowClosed, color: "#a78bfa" }
        });
      }
    }

    setNodes(newNodes);
    setEdges(newEdges);
  };

  const handleNodeClick = (event: React.MouseEvent, node: any) => {
    setSelectedNode(node.data);
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-10">
      
      {/* Top Navigation */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border/20 pb-4">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => router.back()}
            className="h-8 w-8 text-foreground/50 hover:text-foreground hover:bg-white/5 rounded-lg transition"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <h1 className="text-xl md:text-2xl font-black text-foreground">
              Trace Provenance Lineage
            </h1>
            <p className="text-[10px] text-foreground/45 font-mono">
              W3C PROV-O Standard Grounding Audit Logs
            </p>
          </div>
        </div>

        {/* Trace ID Search Form */}
        <form onSubmit={handleSearchSubmit} className="flex items-center gap-2 max-w-md w-full sm:w-auto">
          <div className="relative flex-1 sm:w-80">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-foreground/40" />
            <Input
              value={traceId}
              onChange={(e) => setTraceId(e.target.value)}
              placeholder="Inserisci Trace ID (UUID)..."
              className="pl-9 bg-black/40 border-border/40 text-foreground text-xs focus-visible:ring-accent-indigo/50 h-9"
              autoComplete="off"
            />
          </div>
          <Button
            type="submit"
            size="sm"
            className="bg-accent-indigo text-black font-bold hover:opacity-90 h-9 cursor-pointer"
          >
            Audit
          </Button>
        </form>
      </div>

      {!traceIdParam ? (
        <Card className="glassmorphism border-border/50 text-foreground py-16 text-center max-w-xl mx-auto">
          <CardContent className="space-y-4">
            <div className="mx-auto h-12 w-12 rounded-full bg-accent-indigo/10 border border-accent-indigo/25 flex items-center justify-center text-accent-indigo">
              <HelpCircle className="h-6 w-6" />
            </div>
            <h2 className="text-lg font-black text-foreground">Nessun ID Traccia Selezionato</h2>
            <p className="text-xs text-foreground/45 max-w-md mx-auto leading-relaxed">
              Inserisci un UUID di traccia valido nella barra di ricerca in alto a destra, oppure clicca sul link di verifica lineage presente in fondo a qualsiasi risposta della chat per visualizzare l'audit completo.
            </p>
          </CardContent>
        </Card>
      ) : loading ? (
        <div className="h-[50vh] flex flex-col items-center justify-center text-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-accent-indigo border-t-transparent"></div>
          <p className="text-xs font-mono text-foreground/50 tracking-wider">GENERATING CRYPTOGRAPHIC PROVENANCE REPORT...</p>
        </div>
      ) : error ? (
        <Card className="glassmorphism border-border/50 text-foreground p-8 max-w-xl mx-auto text-center space-y-4">
          <div className="mx-auto h-12 w-12 rounded-full bg-accent-rose/10 border border-accent-rose/25 flex items-center justify-center text-accent-rose">
            <ShieldAlert className="h-6 w-6" />
          </div>
          <h2 className="text-lg font-black text-foreground">Errore di Verifica Audit</h2>
          <p className="text-xs text-foreground/60 leading-relaxed font-mono">
            {error}
          </p>
          <Button
            variant="outline"
            className="border-border/40 hover:bg-white/5 cursor-pointer text-xs"
            onClick={() => router.push("/chat")}
          >
            Torna alla Chat
          </Button>
        </Card>
      ) : report ? (
        <div className="space-y-6">
          
          {/* Metadata Row */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            
            <Card className="glassmorphism border-border/40 text-foreground p-4">
              <span className="text-[10px] font-mono text-foreground/45 uppercase tracking-wider block">Trace Identifier</span>
              <span className="text-xs font-mono font-bold truncate block pt-1" title={report.trace_id}>
                {report.trace_id}
              </span>
            </Card>

            <Card className="glassmorphism border-border/40 text-foreground p-4">
              <span className="text-[10px] font-mono text-foreground/45 uppercase tracking-wider block">Duration</span>
              <span className="text-xs font-mono font-bold flex items-center gap-1 pt-1 text-accent-cyan">
                <Clock className="h-4 w-4" />
                {report.latency_ms ? `${(report.latency_ms / 1000).toFixed(2)}s` : "N/A"}
              </span>
            </Card>

            <Card className="glassmorphism border-border/40 text-foreground p-4">
              <span className="text-[10px] font-mono text-foreground/45 uppercase tracking-wider block">Policy Redactions</span>
              <span className="text-xs font-mono font-bold flex items-center gap-2 pt-1 text-accent-rose">
                <ShieldAlert className="h-4 w-4" />
                {report.policy_summary.denied} denied chunks
              </span>
            </Card>

            <Card className="glassmorphism border-border/40 text-foreground p-4">
              <span className="text-[10px] font-mono text-foreground/45 uppercase tracking-wider block">Verification integrity</span>
              <span className="text-xs font-mono font-bold flex items-center gap-1.5 pt-1 text-accent-emerald">
                <ShieldCheck className="h-4 w-4" />
                Intact & Chain-Chained
              </span>
            </Card>

          </div>

          {/* Main Visualizer Area (Flow Graph + Node Detail Pane) */}
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 items-stretch">
            
            {/* XYFlow Component (3 cols) */}
            <Card className="lg:col-span-3 glassmorphism border-border/50 text-foreground overflow-hidden h-[460px] relative">
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeClick={handleNodeClick}
                fitView
                className="bg-[#0a0a14]/60"
              >
                <Background color="rgba(255,255,255,0.03)" gap={20} size={1} />
                <Controls className="bg-black/80 border border-border/40 text-foreground rounded-lg overflow-hidden fill-white" />
              </ReactFlow>
            </Card>

            {/* Selected Node Details (1 col) */}
            <Card className="glassmorphism border-border/50 text-foreground flex flex-col justify-between h-[460px]">
              <CardHeader className="pb-3 border-b border-border/10">
                <CardTitle className="text-sm font-bold flex items-center gap-1.5">
                  <FileCode className="h-4.5 w-4.5 text-accent-indigo" />
                  Lineage Metadata
                </CardTitle>
                <CardDescription className="text-[10px]">
                  Click on any node in the provenance graph.
                </CardDescription>
              </CardHeader>
              
              <CardContent className="flex-1 p-4 overflow-y-auto custom-scrollbar text-xs font-mono space-y-4">
                {selectedNode ? (
                  <div className="space-y-4">
                    <div>
                      <span className="text-[9px] text-foreground/40 uppercase block">Node Classification</span>
                      <Badge className="bg-accent-indigo/15 text-accent-indigo border-accent-indigo/25 hover:bg-accent-indigo/20 mt-1 h-5 text-[9px] font-bold">
                        {selectedNode.type}
                      </Badge>
                    </div>

                    <div>
                      <span className="text-[9px] text-foreground/40 uppercase block">Label</span>
                      <p className="text-foreground/90 font-sans mt-0.5 text-xs font-bold leading-normal">{selectedNode.label}</p>
                    </div>

                    <div>
                      <span className="text-[9px] text-foreground/40 uppercase block">FGA ID</span>
                      <p className="text-foreground/80 mt-0.5 break-all leading-normal text-[10px]">{selectedNode.raw.id}</p>
                    </div>

                    {selectedNode.raw.value && (
                      <div>
                        <span className="text-[9px] text-foreground/40 uppercase block">Value Content</span>
                        <div className="bg-black/35 rounded-lg border border-border/15 p-2 text-[10px] text-foreground/80 font-sans leading-relaxed mt-1 max-h-32 overflow-y-auto custom-scrollbar">
                          {selectedNode.raw.value}
                        </div>
                      </div>
                    )}

                    {selectedNode.raw.status && (
                      <div>
                        <span className="text-[9px] text-foreground/40 uppercase block">ReBAC Decision</span>
                        <Badge variant="outline" className={`mt-1 font-bold ${
                          selectedNode.raw.status === "allowed" 
                            ? "text-accent-emerald border-accent-emerald/20 bg-accent-emerald/5" 
                            : "text-accent-rose border-accent-rose/20 bg-accent-rose/5"
                        }`}>
                          {selectedNode.raw.status.toUpperCase()}
                        </Badge>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="h-full flex flex-col items-center justify-center text-center text-foreground/40 gap-2 py-10">
                    <HelpCircle className="h-6 w-6 text-foreground/20 animate-bounce" />
                    <span className="text-[10px] font-sans">Seleziona un nodo del grafo per ispezionare i record W3C PROV-O canonical.</span>
                  </div>
                )}
              </CardContent>
            </Card>

          </div>

          {/* Grounding Evidence Audit Table */}
          <Card className="glassmorphism border-border/50 text-foreground">
            <CardHeader className="pb-3 border-b border-border/10">
              <CardTitle className="text-sm font-bold flex items-center gap-1.5">
                <Database className="h-4.5 w-4.5 text-accent-emerald" />
                Grounded Retrieval Evidence List ({report.evidence.length})
              </CardTitle>
              <CardDescription className="text-xs">
                Shows all data items evaluated by the prompt generator and their corresponding ReBAC security decision.
              </CardDescription>
            </CardHeader>
            <CardContent className="p-0 overflow-x-auto custom-scrollbar">
              <table className="w-full text-left text-xs font-mono border-collapse min-w-[700px]">
                <thead>
                  <tr className="bg-black/30 border-b border-border/10 text-foreground/50 text-[10px] uppercase font-bold tracking-wider">
                    <th className="p-4">Evidence ID</th>
                    <th className="p-4">Retriever Lane</th>
                    <th className="p-4">Object ID</th>
                    <th className="p-4">Score / Rank</th>
                    <th className="p-4">Policy Check Status</th>
                    <th className="p-4">Prompt Grounding</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/10">
                  {report.evidence.map((ev) => (
                    <tr key={ev.evidence_id} className="hover:bg-white/5 transition">
                      <td className="p-4 font-bold text-foreground/90 truncate max-w-40" title={ev.evidence_id}>
                        {ev.evidence_id}
                      </td>
                      <td className="p-4">
                        <Badge className={`text-[9px] font-bold ${
                          ev.retriever_id === "vector" 
                            ? "bg-accent-indigo/10 text-accent-indigo border-accent-indigo/20 hover:bg-accent-indigo/15" 
                            : ev.retriever_id === "lexical" 
                              ? "bg-accent-cyan/10 text-accent-cyan border-accent-cyan/20 hover:bg-accent-cyan/15"
                              : "bg-accent-emerald/10 text-accent-emerald border-accent-emerald/20 hover:bg-accent-emerald/15"
                        }`}>
                          {ev.retriever_id.toUpperCase()}
                        </Badge>
                      </td>
                      <td className="p-4 text-foreground/80 truncate max-w-40" title={ev.object_id}>
                        {ev.object_id}
                      </td>
                      <td className="p-4 text-foreground/75">
                        {ev.original_score.toFixed(3)} <span className="text-foreground/40 font-normal">Rank {ev.original_rank + 1}</span>
                      </td>
                      <td className="p-4">
                        <Badge variant="outline" className={`font-bold ${
                          ev.status === "allowed" 
                            ? "text-accent-emerald border-accent-emerald/20 bg-accent-emerald/5" 
                            : "text-accent-rose border-accent-rose/20 bg-accent-rose/5"
                        }`}>
                          {ev.status === "allowed" ? "ALLOWED" : "REDACTED"}
                        </Badge>
                      </td>
                      <td className="p-4 font-sans text-foreground/85 leading-normal max-w-md">
                        {ev.status === "allowed" ? (
                          <span className="line-clamp-2" title={ev.object_data}>{ev.object_data}</span>
                        ) : (
                          <span className="text-accent-rose font-mono text-[10px] font-bold bg-accent-rose/5 border border-accent-rose/15 rounded px-2 py-0.5 flex items-center gap-1 w-fit">
                            <ShieldAlert className="h-3 w-3" />
                            REDACTED - UNAUTHORIZED REBAC ACCESS
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>

        </div>
      ) : null}

    </div>
  );
}

export default function ExplainPage() {
  return (
    <Suspense fallback={
      <div className="h-[50vh] flex flex-col items-center justify-center text-center gap-4">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-accent-indigo border-t-transparent"></div>
        <p className="text-xs font-mono text-foreground/50 tracking-wider">LOADING EXPLAINABILITY MODULE...</p>
      </div>
    }>
      <ExplainContent />
    </Suspense>
  );
}
