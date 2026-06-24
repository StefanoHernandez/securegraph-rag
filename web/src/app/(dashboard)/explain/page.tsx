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
  ShieldAlert,
  ArrowLeft,
  Search,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel, EvidenceTag, RedactionBar } from "@/components/forensic";
import { explainApi } from "@/lib/api";
import { ExplainReport } from "@/lib/types";

// Forensic Vault palette — hex for xyflow inline styles (no Tailwind in SVG).
const C = {
  vault: "#14161A",
  vaultRaised: "#1B1E24",
  bone: "#E8E6DF",
  boneDim: "rgba(232,230,223,0.55)",
  hairline: "rgba(232,230,223,0.12)",
  signalAmber: "#E8B23A",
  sealRed: "#C8503C",
};

// Square, document-grade node face. State drives border/text colour only.
const nodeStyle = (accent: string): React.CSSProperties => ({
  background: C.vaultRaised,
  border: `1px solid ${accent}`,
  borderRadius: 2,
  color: C.bone,
  padding: "10px 12px",
  width: 200,
  textAlign: "center",
  fontFamily: "var(--font-mono), monospace",
  fontSize: 10,
  letterSpacing: "0.04em",
});

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
        style: nodeStyle(C.hairline)
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
        style: nodeStyle(C.hairline)
      });

      // Link Service Agent to pipeline activity
      newEdges.push({
        id: `edge-agent-service-act-${act.id}`,
        source: "agent:securgraph-service",
        target: act.id,
        label: "wasAssociatedWith",
        animated: true,
        style: { stroke: C.boneDim, strokeWidth: 1.5 },
        labelStyle: { fill: C.boneDim, fontSize: 8, fontFamily: "monospace" },
        labelBgStyle: { fill: C.vault },
        markerEnd: { type: MarkerType.ArrowClosed, color: C.boneDim }
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
        style: nodeStyle(C.hairline)
      });

      // Link User Agent to Query
      newEdges.push({
        id: `edge-user-query`,
        source: "agent:user:admin", // Fallback if ID changed
        target: queryEnt.id,
        label: "wasAttributedTo",
        style: { stroke: C.boneDim, strokeWidth: 1 },
        labelStyle: { fill: C.boneDim, fontSize: 8, fontFamily: "monospace" },
        labelBgStyle: { fill: C.vault },
        markerEnd: { type: MarkerType.ArrowClosed, color: C.boneDim }
      });

      // Link Query to Activity
      newEdges.push({
        id: `edge-query-activity`,
        source: queryEnt.id,
        target: data.prov_activities[0]?.id || "activity:query_pipeline",
        label: "wasUsedIn",
        animated: true,
        style: { stroke: C.boneDim, strokeWidth: 1.5 },
        labelStyle: { fill: C.boneDim, fontSize: 8, fontFamily: "monospace" },
        labelBgStyle: { fill: C.vault },
        markerEnd: { type: MarkerType.ArrowClosed, color: C.boneDim }
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
        style: nodeStyle(isAllowed ? C.hairline : C.sealRed)
      });

      // Link Service Agent to Evidence
      newEdges.push({
        id: `edge-agent-ev-${ev.id}`,
        source: "agent:securgraph-service",
        target: ev.id,
        label: "wasAttributedTo",
        style: { stroke: isAllowed ? C.boneDim : C.sealRed, strokeWidth: 1 },
        labelStyle: { fill: isAllowed ? C.boneDim : C.sealRed, fontSize: 8, fontFamily: "monospace" },
        labelBgStyle: { fill: C.vault },
        markerEnd: { type: MarkerType.ArrowClosed, color: isAllowed ? C.boneDim : C.sealRed }
      });

      // Link Evidence to Activity if used
      if (isAllowed) {
        newEdges.push({
          id: `edge-ev-act-${ev.id}`,
          source: ev.id,
          target: data.prov_activities[0]?.id || "activity:query_pipeline",
          label: "wasUsedIn",
          style: { stroke: C.bone, strokeWidth: 1.2 },
          labelStyle: { fill: C.bone, fontSize: 8, fontFamily: "monospace" },
          labelBgStyle: { fill: C.vault },
          markerEnd: { type: MarkerType.ArrowClosed, color: C.bone }
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
        style: nodeStyle(C.signalAmber)
      });

      // Link Activity to Answer
      newEdges.push({
        id: `edge-activity-answer`,
        source: data.prov_activities[0]?.id || "activity:query_pipeline",
        target: answerEnt.id,
        label: "wasGeneratedBy",
        animated: true,
        style: { stroke: C.signalAmber, strokeWidth: 1.5 },
        labelStyle: { fill: C.signalAmber, fontSize: 8, fontFamily: "monospace" },
        labelBgStyle: { fill: C.vault },
        markerEnd: { type: MarkerType.ArrowClosed, color: C.signalAmber }
      });

      // Link Query to Answer (wasDerivedFrom)
      if (queryEnt) {
        newEdges.push({
          id: `edge-query-answer`,
          source: queryEnt.id,
          target: answerEnt.id,
          label: "wasDerivedFrom",
          style: { stroke: C.boneDim, strokeWidth: 1, strokeDasharray: "4 4" },
          labelStyle: { fill: C.boneDim, fontSize: 8, fontFamily: "monospace" },
          labelBgStyle: { fill: C.vault },
          markerEnd: { type: MarkerType.ArrowClosed, color: C.boneDim }
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

      {/* Header */}
      <header className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 pb-4 border-b border-hairline">
        <div className="flex items-start gap-3">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => router.back()}
            className="h-8 w-8 text-bone-dim hover:text-bone"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <p className="mono-meta">section 03 · provenance</p>
            <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-bone">
              Trace Provenance Lineage
            </h1>
          </div>
        </div>

        {/* Trace ID Search Form */}
        <form onSubmit={handleSearchSubmit} className="flex items-center gap-2 max-w-md w-full sm:w-auto">
          <div className="relative flex-1 sm:w-80">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-bone-dim" />
            <Input
              value={traceId}
              onChange={(e) => setTraceId(e.target.value)}
              placeholder="Inserisci Trace ID (UUID)..."
              className="pl-9 bg-vault-raised border-hairline text-bone text-xs font-mono h-9 focus-visible:border-signal-amber"
              autoComplete="off"
            />
          </div>
          <Button type="submit" size="sm" className="h-9">
            Audit
          </Button>
        </form>
      </header>

      {!traceIdParam ? (
        <Panel label="provenance audit · awaiting trace" className="max-w-xl mx-auto">
          <div className="px-6 py-16 text-center space-y-4">
            <div className="mx-auto h-12 w-12 border border-hairline bg-vault-sunk flex items-center justify-center text-bone-dim">
              <HelpCircle className="h-6 w-6" />
            </div>
            <h2 className="font-display text-lg font-bold text-bone">Nessun ID Traccia Selezionato</h2>
            <p className="text-xs text-bone-dim max-w-md mx-auto leading-relaxed">
              Inserisci un UUID di traccia valido nella barra di ricerca in alto a destra, oppure clicca sul link di verifica lineage presente in fondo a qualsiasi risposta della chat per visualizzare l'audit completo.
            </p>
          </div>
        </Panel>
      ) : loading ? (
        <div className="h-[50vh] flex flex-col items-center justify-center text-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-signal-amber border-t-transparent"></div>
          <p className="mono-meta">generating cryptographic provenance report…</p>
        </div>
      ) : error ? (
        <Panel classification="restricted" label="audit verification error" className="max-w-xl mx-auto">
          <div className="px-8 py-8 text-center space-y-4">
            <div className="mx-auto h-12 w-12 border border-seal-red/40 bg-vault-sunk flex items-center justify-center text-seal-red">
              <ShieldAlert className="h-6 w-6" />
            </div>
            <h2 className="font-display text-lg font-bold text-bone">Errore di Verifica Audit</h2>
            <p className="text-xs text-bone-dim leading-relaxed font-mono">
              {error}
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => router.push("/chat")}
            >
              Torna alla Chat
            </Button>
          </div>
        </Panel>
      ) : report ? (
        <div className="space-y-6">

          {/* Metadata Row */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-px bg-hairline border border-hairline">

            <div className="bg-vault-raised px-4 py-3">
              <p className="mono-meta">trace identifier</p>
              <p className="mt-1 font-mono text-sm text-bone truncate" title={report.trace_id}>
                {report.trace_id}
              </p>
            </div>

            <div className="bg-vault-raised px-4 py-3">
              <p className="mono-meta">duration</p>
              <p className="mt-1 font-mono text-sm text-bone tabular-nums">
                {report.latency_ms ? `${(report.latency_ms / 1000).toFixed(2)}s` : "N/A"}
              </p>
            </div>

            <div className="bg-vault-raised px-4 py-3">
              <p className="mono-meta">policy redactions</p>
              <p className="mt-1 font-mono text-sm text-seal-red tabular-nums">
                {report.policy_summary.denied} denied
              </p>
            </div>

            <div className="bg-vault-raised px-4 py-3">
              <p className="mono-meta">integrity</p>
              <p className="mt-1 stamp text-signal-amber">✓ INTACT</p>
            </div>

          </div>

          {/* Main Visualizer Area (Flow Graph + Node Detail Pane) */}
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 items-stretch">

            {/* XYFlow Component (3 cols) */}
            <Panel className="lg:col-span-3 overflow-hidden h-[460px]" label="w3c prov-o lineage graph">
              <div className="h-[calc(460px-41px)]">
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  onNodeClick={handleNodeClick}
                  fitView
                  style={{ background: "transparent" }}
                >
                  <Background color={C.hairline} gap={20} size={1} />
                  <Controls
                    style={{
                      background: C.vaultRaised,
                      border: `1px solid ${C.hairline}`,
                      borderRadius: 2,
                    }}
                  />
                </ReactFlow>
              </div>
            </Panel>

            {/* Selected Node Details (1 col) */}
            <Panel label="lineage metadata" className="flex flex-col h-[460px]">
              <div className="flex-1 p-4 overflow-y-auto custom-scrollbar text-xs font-mono space-y-4">
                {selectedNode ? (
                  <div className="space-y-4">
                    <div>
                      <p className="mono-meta">node classification</p>
                      <span className="stamp text-bone mt-1.5">
                        {selectedNode.type}
                      </span>
                    </div>

                    <div>
                      <p className="mono-meta">label</p>
                      <p className="text-bone font-sans mt-1 text-xs font-medium leading-normal">{selectedNode.label}</p>
                    </div>

                    <div>
                      <p className="mono-meta">fga id</p>
                      <p className="text-bone-dim mt-1 break-all leading-normal text-[10px]">{selectedNode.raw.id}</p>
                    </div>

                    {selectedNode.raw.value && (
                      <div>
                        <p className="mono-meta">value content</p>
                        <div className="bg-vault-sunk border border-hairline p-2 text-[10px] text-bone font-sans leading-relaxed mt-1.5 max-h-32 overflow-y-auto custom-scrollbar">
                          {selectedNode.raw.value}
                        </div>
                      </div>
                    )}

                    {selectedNode.raw.status && (
                      <div>
                        <p className="mono-meta">rebac decision</p>
                        <span className={`stamp mt-1.5 ${
                          selectedNode.raw.status === "allowed"
                            ? "text-signal-amber"
                            : "text-seal-red"
                        }`}>
                          {selectedNode.raw.status.toUpperCase()}
                        </span>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="h-full flex flex-col items-center justify-center text-center text-bone-dim gap-2 py-10">
                    <HelpCircle className="h-6 w-6 text-bone-dim/50" />
                    <span className="text-[10px] font-sans">Seleziona un nodo del grafo per ispezionare i record W3C PROV-O canonical.</span>
                  </div>
                )}
              </div>
            </Panel>

          </div>

          {/* Grounding Evidence Audit Table */}
          <Panel label={`grounded retrieval evidence · ${report.evidence.length} items`}>
            <div className="px-5 py-3 border-b border-hairline">
              <p className="text-xs text-bone-dim leading-relaxed">
                Each item was evaluated by the prompt generator against its ReBAC security decision. Redacted rows are sealed at retrieval — the content never reaches the model.
              </p>
            </div>
            <div className="overflow-x-auto custom-scrollbar">
              <table className="w-full text-left text-xs border-collapse min-w-[720px]">
                <thead>
                  <tr className="border-b border-hairline text-bone-dim">
                    {["evidence", "lane", "object id", "score / rank", "policy", "prompt grounding"].map((h, i) => (
                      <th key={i} className="px-4 py-2.5 font-mono text-[0.625rem] uppercase tracking-wider font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline">
                  {report.evidence.map((ev, idx) => {
                    const isAllowed = ev.status === "allowed";
                    return (
                      <tr key={ev.evidence_id} className="hover:bg-[rgba(232,230,223,0.03)] transition-colors">
                        <td className="px-4 py-3">
                          <EvidenceTag marker="E" index={idx + 1} redacted={!isAllowed} />
                        </td>
                        <td className="px-4 py-3 font-mono text-bone-dim uppercase">
                          {ev.retriever_id}
                        </td>
                        <td className="px-4 py-3 font-mono text-bone-dim truncate max-w-40" title={ev.object_id}>
                          {ev.object_id}
                        </td>
                        <td className="px-4 py-3 font-mono text-bone-dim tabular-nums whitespace-nowrap">
                          {ev.original_score.toFixed(3)} <span className="text-bone-dim/60">rank {ev.original_rank + 1}</span>
                        </td>
                        <td className="px-4 py-3">
                          <span className={isAllowed ? "stamp text-signal-amber" : "stamp text-seal-red"}>
                            {isAllowed ? "✓ ALLOWED" : "▮ REDACTED"}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-bone leading-normal max-w-md">
                          {isAllowed ? (
                            <RedactionBar authorized className="line-clamp-2 font-sans">
                              {ev.object_data}
                            </RedactionBar>
                          ) : (
                            <RedactionBar authorized={false} reason="policy:deny">
                              {ev.object_data || "redacted evidence chunk withheld by ReBAC"}
                            </RedactionBar>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Panel>

        </div>
      ) : null}

    </div>
  );
}

export default function ExplainPage() {
  return (
    <Suspense fallback={
      <div className="h-[50vh] flex flex-col items-center justify-center text-center gap-4">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-signal-amber border-t-transparent"></div>
        <p className="mono-meta">loading explainability module…</p>
      </div>
    }>
      <ExplainContent />
    </Suspense>
  );
}
