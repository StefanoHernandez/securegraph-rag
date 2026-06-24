"use client";

import * as React from "react";
import ReactMarkdown from "react-markdown";
import { ExplainReport } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  RedactionBar,
  TrustVerdict,
  TamperChain,
  EvidenceTag,
} from "@/components/forensic";
import { TimelineStage } from "./TimelineStage";
import { ProvLineagePanel } from "./ProvLineagePanel";

function Meter({ label, value }: { label: string; value: number | null | undefined }) {
  const pct = value == null ? 0 : Math.round(value * 100);
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between">
        <span className="mono-meta">{label}</span>
        <span className="font-mono text-sm text-bone tabular-nums">
          {value == null ? "—" : `${pct}%`}
        </span>
      </div>
      <div className="h-px w-full bg-hairline">
        <div className="h-px bg-bone" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function CustodyTimeline({ report }: { report: ExplainReport }) {
  const [showEvidence, setShowEvidence] = React.useState(false);

  const operator =
    report.prov_agents.find((a) => a.id.includes("user"))?.label || "operator";

  // Group evidence by retrieval lane for the aggregate counts.
  const laneCounts = report.evidence.reduce<Record<string, number>>((acc, e) => {
    acc[e.retriever_id] = (acc[e.retriever_id] || 0) + 1;
    return acc;
  }, {});
  const allowed = report.evidence.filter((e) => e.status === "allowed").length;
  const redacted = report.evidence.length - allowed;

  const c = report.consensus;
  const hasConsensus = !!c && (c.agreement_score != null || c.high_trust != null);

  return (
    <div className="space-y-6">
      {/* Metadata strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-px bg-hairline border border-hairline">
        <div className="bg-vault-raised px-4 py-3">
          <p className="mono-meta">trace identifier</p>
          <p className="mt-1 font-mono text-sm text-bone truncate" title={report.trace_id}>
            {report.trace_id}
          </p>
        </div>
        <div className="bg-vault-raised px-4 py-3">
          <p className="mono-meta">duration</p>
          <p className="mt-1 font-mono text-sm text-bone tabular-nums">
            {report.latency_ms ? `${(report.latency_ms / 1000).toFixed(2)}s` : "—"}
          </p>
        </div>
        <div className="bg-vault-raised px-4 py-3">
          <p className="mono-meta">policy redactions</p>
          <p
            className={cn(
              "mt-1 font-mono text-sm tabular-nums",
              redacted > 0 ? "text-seal-red" : "text-bone"
            )}
          >
            {redacted} redacted
          </p>
        </div>
        <div className="bg-vault-raised px-4 py-3">
          <p className="mono-meta">integrity</p>
          <p className="mt-1 stamp text-signal-amber">✓ INTACT</p>
        </div>
      </div>

      {/* The custody chain */}
      <ol className="m-0 list-none p-0">
        {/* ① Request */}
        <TimelineStage
          index={1}
          title="request"
          headRight={
            <span className="mono-meta normal-case">{operator}</span>
          }
        >
          <div className="px-4 py-3">
            <p className="mono-meta">query</p>
            <p className="mt-1.5 text-sm text-bone leading-relaxed">
              {report.query}
            </p>
          </div>
        </TimelineStage>

        {/* ② Retrieval */}
        <TimelineStage
          index={2}
          title="retrieval · evidence gathered"
          headRight={
            <span
              className={
                redacted > 0 ? "stamp text-seal-red" : "stamp text-bone-dim"
              }
            >
              {redacted > 0 ? `▮ ${redacted} REDACTED` : "NO REDACTIONS"}
            </span>
          }
        >
          <div className="px-4 py-3 space-y-3">
            <div className="flex flex-wrap gap-2">
              {Object.entries(laneCounts).map(([lane, n]) => (
                <span
                  key={lane}
                  className="border border-hairline px-2 py-1 font-mono text-[0.6875rem] text-bone"
                >
                  {lane} · {n}
                </span>
              ))}
              {report.evidence.length === 0 && (
                <span className="mono-meta">no evidence on record</span>
              )}
            </div>
            {report.evidence.length > 0 && (
              <button
                onClick={() => setShowEvidence((s) => !s)}
                className="mono-meta hover:text-signal-amber transition-colors"
              >
                {showEvidence ? "hide evidence ▾" : `inspect ${report.evidence.length} items ▸`}
              </button>
            )}
          </div>

          {showEvidence && (
            <div className="overflow-x-auto custom-scrollbar border-t border-hairline">
              <table className="w-full text-left text-xs border-collapse min-w-[680px]">
                <thead>
                  <tr className="border-b border-hairline text-bone-dim">
                    {["evidence", "lane", "object id", "score", "policy", "grounding"].map(
                      (h, i) => (
                        <th
                          key={i}
                          className="px-4 py-2.5 font-mono text-[0.625rem] uppercase tracking-wider font-medium"
                        >
                          {h}
                        </th>
                      )
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline">
                  {report.evidence.map((ev, idx) => {
                    const isAllowed = ev.status === "allowed";
                    return (
                      <tr key={ev.evidence_id} className="hover:bg-[rgba(232,230,223,0.03)]">
                        <td className="px-4 py-3">
                          <EvidenceTag marker="E" index={idx + 1} redacted={!isAllowed} />
                        </td>
                        <td className="px-4 py-3 font-mono text-bone-dim uppercase">
                          {ev.retriever_id}
                        </td>
                        <td
                          className="px-4 py-3 font-mono text-bone-dim truncate max-w-40"
                          title={ev.object_id}
                        >
                          {ev.object_id}
                        </td>
                        <td className="px-4 py-3 font-mono text-bone-dim tabular-nums whitespace-nowrap">
                          {ev.original_score.toFixed(3)}
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
                              {ev.object_data || "redacted evidence withheld by ReBAC"}
                            </RedactionBar>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </TimelineStage>

        {/* ③ Consensus */}
        <TimelineStage
          index={3}
          title="consensus · cross-retriever verdict"
          sealed={hasConsensus && !!c?.high_trust}
          headRight={
            hasConsensus ? (
              <TrustVerdict
                highTrust={!!c?.high_trust}
                score={c?.agreement_score ?? undefined}
              />
            ) : (
              <span className="mono-meta">not recorded</span>
            )
          }
        >
          {hasConsensus ? (
            <div className="px-4 py-4 grid grid-cols-1 sm:grid-cols-3 gap-5">
              <Meter label="agreement" value={c?.agreement_score} />
              <Meter label="entity overlap" value={c?.entity_overlap} />
              <Meter label="nli entailment" value={c?.claim_nli_score} />
            </div>
          ) : (
            <p className="px-4 py-3 text-xs text-bone-dim leading-relaxed">
              Consensus was not recorded for this trace (created before consensus
              auditing was enabled). Re-run the query to capture the verdict.
            </p>
          )}
        </TimelineStage>

        {/* ④ Synthesis */}
        <TimelineStage
          index={4}
          title="synthesis · sealed answer"
          isLast
          sealed
          headRight={
            <span className="mono-meta">
              {report.latency_ms ? `${(report.latency_ms / 1000).toFixed(2)}s` : "—"}
            </span>
          }
        >
          <div className="px-4 py-4 space-y-4">
            <div className="prose prose-invert prose-sm max-w-none text-bone/90 leading-relaxed">
              <ReactMarkdown>{report.answer || "_No answer recorded._"}</ReactMarkdown>
            </div>
            <div className="flex items-center gap-3 border-t border-hairline pt-3">
              <span className="mono-meta">custody seal</span>
              <TamperChain count={report.evidence.length + 1} status="intact" />
              <span className="stamp text-signal-amber">✓ SEALED</span>
            </div>
          </div>
        </TimelineStage>
      </ol>

      {/* Aggregated W3C PROV-O (collapsed) */}
      <ProvLineagePanel report={report} />
    </div>
  );
}
