"use client";

import React, { useState } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import { useTranslations } from "next-intl";
import { QueryResponse } from "@/lib/types";
import { TrustVerdict, EvidenceTag } from "@/components/forensic";
import { cn } from "@/lib/utils";
import { ChevronDown, ChevronUp, ArrowUpRight, Info } from "lucide-react";

interface AnswerCardProps {
  response: QueryResponse;
  /** Retrieval mode the query ran with; single-lane modes get a "consensus N/A" verdict. */
  retrievalMode?: string;
}

const SINGLE_LANE_MODES = new Set(["vector", "lexical", "graph"]);

function Meter({
  label,
  value,
}: {
  label: string;
  value: number | null | undefined;
}) {
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

export default function AnswerCard({ response, retrievalMode }: AnswerCardProps) {
  const [showCitations, setShowCitations] = useState(false);
  const t = useTranslations("answerCard");
  const { answer, confidence, channels, citations, trace_id, warnings } =
    response;
  const singleLane = retrievalMode ? SINGLE_LANE_MODES.has(retrievalMode) : false;

  const channelRows = [
    { key: "vector", label: t("vectorLane"), unit: t("chunks"), ch: channels.vector },
    { key: "lexical", label: t("lexicalLane"), unit: t("terms"), ch: channels.lexical },
    { key: "graph", label: t("graphLane"), unit: t("paths"), ch: channels.graph },
    {
      key: "community",
      label: t("globalLane"),
      unit: t("nodes"),
      ch: channels.community,
    },
  ];

  return (
    <div className="panel">
      {/* Verdict header */}
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-hairline px-5 py-3">
        <TrustVerdict
          highTrust={confidence.high_trust}
          score={confidence.agreement_score ?? undefined}
          notApplicableLabel={singleLane ? t("consensusNA") : undefined}
        />
        <Link
          href={`/explain?trace_id=${trace_id}`}
          className="inline-flex items-center gap-1.5 font-mono text-[0.6875rem] uppercase tracking-wider text-bone-dim hover:text-signal-amber transition-colors"
        >
          {t("verifyLineage")} · {trace_id.slice(0, 8)}
          <ArrowUpRight className="h-3.5 w-3.5" />
        </Link>
      </header>

      <div className="px-5 py-5 space-y-6">
        {warnings && warnings.length > 0 && (
          <div className="classification-gutter px-3 py-2.5 text-xs text-seal-red space-y-1" style={{ ["--gutter-color" as string]: "var(--seal-red)" }}>
            {warnings.map((w, idx) => (
              <div key={idx} className="flex items-start gap-2">
                <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
                <span>{w}</span>
              </div>
            ))}
          </div>
        )}

        {/* Finding body */}
        <div className="declassify prose prose-invert prose-sm max-w-none text-bone/90 leading-relaxed">
          <ReactMarkdown
            components={{
              p: ({ children }) => <p className="mb-3">{children}</p>,
              ul: ({ children }) => <ul className="list-disc pl-5 mb-3 space-y-1">{children}</ul>,
              ol: ({ children }) => <ol className="list-decimal pl-5 mb-3 space-y-1">{children}</ol>,
              code: ({ children }) => (
                <code className="bg-vault-sunk border border-hairline px-1.5 py-0.5 font-mono text-xs text-bone">
                  {children}
                </code>
              ),
              pre: ({ children }) => (
                <pre className="bg-vault-sunk border border-hairline p-4 font-mono text-xs text-bone/80 overflow-x-auto my-3 custom-scrollbar">
                  {children}
                </pre>
              ),
            }}
          >
            {answer}
          </ReactMarkdown>
        </div>

        {/* Consensus meters */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-5 border-t border-hairline pt-5">
          <Meter label={t("consensus")} value={confidence.agreement_score} />
          <Meter label={t("entityOverlap")} value={confidence.entity_overlap} />
          <Meter label={t("nliEntailment")} value={confidence.claim_nli_score} />
          <Meter label={t("evidenceCover")} value={confidence.evidence_coverage} />
        </div>

        {/* Channels */}
        <div className="space-y-2">
          <p className="mono-meta">{t("retrievalChannels")}</p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-hairline border border-hairline">
            {channelRows.map((r) => (
              <div key={r.key} className="bg-vault-raised px-3 py-2.5">
                <p className="text-xs text-bone">{r.label}</p>
                <p
                  className={cn(
                    "mt-1 font-mono text-xs",
                    r.ch ? "text-signal-amber" : "text-bone-dim"
                  )}
                >
                  {r.ch ? `${r.ch.evidence_count} ${r.unit}` : t("inactive")}
                </p>
              </div>
            ))}
          </div>
        </div>

        {/* Citations */}
        {citations && citations.length > 0 && (
          <div className="border border-hairline">
            <button
              onClick={() => setShowCitations(!showCitations)}
              className="w-full flex items-center justify-between px-4 py-2.5 text-xs text-bone hover:bg-[rgba(232,230,223,0.03)] transition-colors"
            >
              <span className="mono-meta">
                {t("evidenceCited")} · {citations.length}
              </span>
              {showCitations ? (
                <ChevronUp className="h-4 w-4 text-bone-dim" />
              ) : (
                <ChevronDown className="h-4 w-4 text-bone-dim" />
              )}
            </button>
            {showCitations && (
              <ul className="border-t border-hairline divide-y divide-hairline max-h-56 overflow-y-auto custom-scrollbar">
                {citations.map((c, idx) => (
                  <li key={idx} className="px-4 py-2.5 space-y-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2">
                        <EvidenceTag index={idx + 1} />
                        <span className="text-xs text-bone truncate">{c.label}</span>
                      </span>
                      <span className="font-mono text-[0.625rem] text-bone-dim truncate max-w-[12rem]">
                        {c.evidence_id}
                      </span>
                    </div>
                    <div className="mono-meta flex flex-wrap gap-x-3 gap-y-1 normal-case">
                      {c.document_id && (
                        <span>
                          {t("doc")} {c.document_id}
                        </span>
                      )}
                      {c.chunk_id && (
                        <span>
                          {t("chunk")} {c.chunk_id}
                        </span>
                      )}
                      {c.page_start != null && (
                        <span>
                          {t("pp")} {c.page_start}–{c.page_end}
                        </span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
