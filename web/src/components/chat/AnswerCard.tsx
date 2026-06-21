"use client";

import React, { useState } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import { QueryResponse } from "@/lib/types";
import {
  ShieldCheck,
  ShieldAlert,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  BookOpen,
  Info,
  Database,
  Hash,
  Share2
} from "lucide-react";

interface AnswerCardProps {
  response: QueryResponse;
}

export default function AnswerCard({ response }: AnswerCardProps) {
  const [showCitations, setShowCitations] = useState(false);
  const { answer, confidence, channels, citations, trace_id, warnings } = response;

  // Determine trust status badge
  const getTrustBadge = () => {
    if (confidence.high_trust) {
      return (
        <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-accent-emerald/10 text-accent-emerald border border-accent-emerald/20 text-xs font-bold font-mono uppercase tracking-wider shadow-sm">
          <ShieldCheck className="h-4 w-4" />
          High Trust Verified
        </span>
      );
    }
    return (
      <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-accent-amber/10 text-accent-amber border border-accent-amber/20 text-xs font-bold font-mono uppercase tracking-wider">
        <ShieldAlert className="h-4 w-4" />
        Standard Consensus
      </span>
    );
  };

  // Convert float to percentage string
  const toPercent = (val: number | null | undefined) => {
    if (val === null || val === undefined) return "N/A";
    return `${(val * 100).toFixed(0)}%`;
  };

  return (
    <div className="space-y-6">
      {/* Trust & Confidence Overview */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border/10 pb-4">
        {getTrustBadge()}
        
        <Link href={`/explain?trace_id=${trace_id}`}>
          <span className="inline-flex items-center gap-1.5 text-xs text-accent-cyan hover:underline font-mono cursor-pointer bg-accent-cyan/5 border border-accent-cyan/15 rounded-lg px-3 py-1.5 transition">
            Verify Lineage Trace
            <ExternalLink className="h-3 w-3" />
          </span>
        </Link>
      </div>

      {/* Warnings Panel */}
      {warnings && warnings.length > 0 && (
        <div className="p-3 rounded-lg bg-accent-rose/10 border border-accent-rose/20 text-accent-rose text-xs space-y-1">
          {warnings.map((w, idx) => (
            <div key={idx} className="flex items-start gap-2">
              <Info className="h-4 w-4 mt-0.5 flex-shrink-0" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}

      {/* Main Answer text rendered as Markdown */}
      <div className="prose prose-invert prose-sm max-w-none text-foreground/90 leading-relaxed font-sans space-y-4">
        <ReactMarkdown
          components={{
            p: ({ children }) => <p className="mb-4">{children}</p>,
            ul: ({ children }) => <ul className="list-disc pl-6 mb-4 space-y-1">{children}</ul>,
            ol: ({ children }) => <ol className="list-decimal pl-6 mb-4 space-y-1">{children}</ol>,
            li: ({ children }) => <li>{children}</li>,
            code: ({ children }) => (
              <code className="bg-white/5 border border-white/10 rounded px-1.5 py-0.5 font-mono text-xs text-accent-indigo">
                {children}
              </code>
            ),
            pre: ({ children }) => (
              <pre className="bg-black/35 border border-border/50 rounded-lg p-4 font-mono text-xs text-foreground/80 overflow-x-auto my-4 custom-scrollbar">
                {children}
              </pre>
            ),
          }}
        >
          {answer}
        </ReactMarkdown>
      </div>

      {/* Diagnostic & Confidence Gauges */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 bg-black/20 border border-border/30 rounded-xl p-4">
        {/* Metric 1 */}
        <div className="space-y-1">
          <span className="text-[10px] text-foreground/45 uppercase tracking-wider font-mono">Consensus Score</span>
          <div className="flex items-baseline gap-1.5">
            <span className="text-lg font-bold text-foreground">{toPercent(confidence.agreement_score)}</span>
          </div>
          <div className="w-full bg-white/5 h-1.5 rounded-full overflow-hidden">
            <div
              className="bg-accent-indigo h-full rounded-full"
              style={{ width: `${(confidence.agreement_score || 0) * 100}%` }}
            />
          </div>
        </div>

        {/* Metric 2 */}
        <div className="space-y-1">
          <span className="text-[10px] text-foreground/45 uppercase tracking-wider font-mono">Entity Overlap</span>
          <div className="flex items-baseline gap-1.5">
            <span className="text-lg font-bold text-foreground">{toPercent(confidence.entity_overlap)}</span>
          </div>
          <div className="w-full bg-white/5 h-1.5 rounded-full overflow-hidden">
            <div
              className="bg-accent-cyan h-full rounded-full"
              style={{ width: `${(confidence.entity_overlap || 0) * 100}%` }}
            />
          </div>
        </div>

        {/* Metric 3 */}
        <div className="space-y-1">
          <span className="text-[10px] text-foreground/45 uppercase tracking-wider font-mono">NLI Entailment</span>
          <div className="flex items-baseline gap-1.5">
            <span className="text-lg font-bold text-foreground">{toPercent(confidence.claim_nli_score)}</span>
          </div>
          <div className="w-full bg-white/5 h-1.5 rounded-full overflow-hidden">
            <div
              className="bg-accent-emerald h-full rounded-full"
              style={{ width: `${(confidence.claim_nli_score || 0) * 100}%` }}
            />
          </div>
        </div>

        {/* Metric 4 */}
        <div className="space-y-1">
          <span className="text-[10px] text-foreground/45 uppercase tracking-wider font-mono">Evidence Cover</span>
          <div className="flex items-baseline gap-1.5">
            <span className="text-lg font-bold text-foreground">{toPercent(confidence.evidence_coverage)}</span>
          </div>
          <div className="w-full bg-white/5 h-1.5 rounded-full overflow-hidden">
            <div
              className="bg-accent-violet h-full rounded-full"
              style={{ width: `${(confidence.evidence_coverage || 0) * 100}%` }}
            />
          </div>
        </div>
      </div>

      {/* Retriever Channels Breakdown */}
      <div className="space-y-2">
        <h4 className="text-xs font-semibold text-foreground/60 uppercase tracking-wider font-mono">Active Evidence Channels</h4>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {/* Channel Vector */}
          <div className="p-3 rounded-lg bg-black/15 border border-border/20 flex flex-col justify-between h-20">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-foreground/80">Vector Lane</span>
              <Database className="h-3.5 w-3.5 text-accent-indigo" />
            </div>
            <span className="text-xs font-mono text-foreground/50">
              {channels.vector ? `${channels.vector.evidence_count} chunks` : "Disabled"}
            </span>
          </div>

          {/* Channel Lexical */}
          <div className="p-3 rounded-lg bg-black/15 border border-border/20 flex flex-col justify-between h-20">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-foreground/80">Lexical Lane</span>
              <Hash className="h-3.5 w-3.5 text-accent-cyan" />
            </div>
            <span className="text-xs font-mono text-foreground/50">
              {channels.lexical ? `${channels.lexical.evidence_count} terms` : "Disabled"}
            </span>
          </div>

          {/* Channel Graph */}
          <div className="p-3 rounded-lg bg-black/15 border border-border/20 flex flex-col justify-between h-20">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-foreground/80">Graph Lane</span>
              <Share2 className="h-3.5 w-3.5 text-accent-emerald" />
            </div>
            <span className="text-xs font-mono text-foreground/50">
              {channels.graph ? `${channels.graph.evidence_count} paths` : "Disabled"}
            </span>
          </div>

          {/* Channel Community */}
          <div className="p-3 rounded-lg bg-black/15 border border-border/20 flex flex-col justify-between h-20">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-foreground/80">Global Lane</span>
              <BookOpen className="h-3.5 w-3.5 text-accent-violet" />
            </div>
            <span className="text-xs font-mono text-foreground/50">
              {channels.community ? `${channels.community.evidence_count} nodes` : "Disabled"}
            </span>
          </div>
        </div>
      </div>

      {/* Citations Accordion */}
      {citations && citations.length > 0 && (
        <div className="border border-border/30 rounded-lg overflow-hidden glassmorphism">
          <button
            onClick={() => setShowCitations(!showCitations)}
            className="w-full flex items-center justify-between p-3 text-xs font-semibold text-foreground/80 hover:bg-white/5 transition"
          >
            <span className="flex items-center gap-2">
              <BookOpen className="h-4 w-4 text-accent-indigo" />
              Source Citations ({citations.length})
            </span>
            {showCitations ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>

          {showCitations && (
            <div className="border-t border-border/10 p-3 divide-y divide-border/10 text-xs font-mono overflow-y-auto max-h-56 custom-scrollbar">
              {citations.map((c, idx) => (
                <div key={idx} className="py-2 first:pt-0 last:pb-0 space-y-1">
                  <div className="flex justify-between items-center gap-2">
                    <span className="text-accent-indigo font-bold">[{idx + 1}] {c.label}</span>
                    <span className="text-foreground/45 text-[10px] truncate max-w-xs">{c.evidence_id}</span>
                  </div>
                  <div className="text-[10px] text-foreground/50 flex flex-wrap gap-x-3 gap-y-1">
                    {c.document_id && <span>doc_id: <b className="text-foreground/80">{c.document_id}</b></span>}
                    {c.chunk_id && <span>chunk_id: <b className="text-foreground/80">{c.chunk_id}</b></span>}
                    {c.page_start !== null && <span>pages: <b className="text-foreground/80">{c.page_start} - {c.page_end}</b></span>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
