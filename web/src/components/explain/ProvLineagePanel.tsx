"use client";

import * as React from "react";
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  MarkerType,
  type Node,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
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

const nodeStyle = (accent: string): React.CSSProperties => ({
  background: C.vaultRaised,
  border: `1px solid ${accent}`,
  borderRadius: 2,
  color: C.bone,
  padding: "8px 10px",
  width: 168,
  textAlign: "center",
  fontFamily: "var(--font-mono), monospace",
  fontSize: 10,
  letterSpacing: "0.04em",
});

const edge = (accent: string, label: string, animated = false): Partial<Edge> => ({
  label,
  animated,
  style: { stroke: accent, strokeWidth: 1.2 },
  labelStyle: { fill: accent, fontSize: 8, fontFamily: "monospace" },
  labelBgStyle: { fill: C.vault },
  markerEnd: { type: MarkerType.ArrowClosed, color: accent },
});

/**
 * Aggregated W3C PROV-O lineage for the standards/demo angle. Unlike the old
 * one-node-per-evidence graph, all evidence collapses into a single
 * "Evidence (N)" node plus a single "Redacted (M)" node, so the canonical PROV
 * shape (user -> service -> activity -> answer) stays readable at any scale.
 * Collapsed by default behind a <details>.
 */
export function ProvLineagePanel({ report }: { report: ExplainReport }) {
  const allowed = report.evidence.filter((e) => e.status === "allowed").length;
  const denied = report.evidence.length - allowed;

  const { nodes, edges } = React.useMemo<{ nodes: Node[]; edges: Edge[] }>(() => {
    const n: Node[] = [
      { id: "user", position: { x: 0, y: 60 }, data: { label: "User / Operator" }, style: nodeStyle(C.hairline) },
      { id: "service", position: { x: 0, y: 200 }, data: { label: "SecureGraph Service" }, style: nodeStyle(C.hairline) },
      { id: "query", position: { x: 230, y: 60 }, data: { label: "Query" }, style: nodeStyle(C.hairline) },
      { id: "activity", position: { x: 230, y: 200 }, data: { label: "Graph-RAG Pipeline" }, style: nodeStyle(C.hairline) },
      { id: "answer", position: { x: 470, y: 130 }, data: { label: "Answer" }, style: nodeStyle(C.signalAmber) },
      { id: "evidence", position: { x: 230, y: 330 }, data: { label: `Evidence (${allowed})` }, style: nodeStyle(C.hairline) },
    ];
    const e: Edge[] = [
      { id: "e-user-query", source: "user", target: "query", ...edge(C.boneDim, "wasAttributedTo") },
      { id: "e-service-act", source: "service", target: "activity", ...edge(C.boneDim, "wasAssociatedWith", true) },
      { id: "e-query-act", source: "query", target: "activity", ...edge(C.boneDim, "wasUsedIn", true) },
      { id: "e-ev-act", source: "evidence", target: "activity", ...edge(C.bone, "wasUsedIn") },
      { id: "e-act-answer", source: "activity", target: "answer", ...edge(C.signalAmber, "wasGeneratedBy", true) },
      { id: "e-query-answer", source: "query", target: "answer", ...edge(C.boneDim, "wasDerivedFrom") },
    ];
    if (denied > 0) {
      n.push({
        id: "redacted",
        position: { x: 470, y: 330 },
        data: { label: `Redacted (${denied})` },
        style: nodeStyle(C.sealRed),
      });
      e.push({
        id: "e-redacted",
        source: "service",
        target: "redacted",
        ...edge(C.sealRed, "policy:deny"),
      });
    }
    return { nodes: n, edges: e };
  }, [allowed, denied]);

  const [flowNodes, , onNodesChange] = useNodesState<Node>(nodes);
  const [flowEdges, , onEdgesChange] = useEdgesState<Edge>(edges);

  return (
    <details className="panel group">
      <summary className="flex cursor-pointer items-center justify-between gap-3 px-4 py-2.5 select-none">
        <span className="mono-meta">w3c prov-o lineage · aggregated</span>
        <span className="mono-meta group-open:hidden">show ▸</span>
        <span className="mono-meta hidden group-open:inline">hide ▾</span>
      </summary>
      <div className="h-[360px] border-t border-hairline">
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          fitView
          proOptions={{ hideAttribution: true }}
          style={{ background: "transparent" }}
        >
          <Background color={C.hairline} gap={20} size={1} />
          <Controls
            style={{ background: C.vaultRaised, border: `1px solid ${C.hairline}`, borderRadius: 2 }}
          />
        </ReactFlow>
      </div>
    </details>
  );
}
