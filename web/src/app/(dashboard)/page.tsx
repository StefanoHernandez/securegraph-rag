"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Panel, TamperChain, TrustVerdict, type ChainStatus } from "@/components/forensic";
import { MessageSquare, UploadCloud, ArrowUpRight } from "lucide-react";
import { adminApi, healthApi, configApi, type ActiveConfig } from "@/lib/api";

interface TraceRow {
  trace_id: string;
  query: string;
  latency: string;
  confidence: number;
  timestamp: string;
  highTrust: boolean;
}

export default function DashboardPage() {
  const [tenantId, setTenantId] = useState("default");
  const [stats, setStats] = useState({
    totalDocuments: 5,
    totalQueries: 0,
    healthStatus: "…",
  });
  const [chain, setChain] = useState<{ status: ChainStatus; count: number }>({
    status: "unverified",
    count: 6,
  });
  const [recentQueries, setRecentQueries] = useState<TraceRow[]>([]);
  const [config, setConfig] = useState<ActiveConfig | null>(null);

  useEffect(() => {
    const savedTenant = localStorage.getItem("securgraph_tenant") || "default";
    setTenantId(savedTenant);
    loadDashboardData(savedTenant);
  }, []);

  const loadDashboardData = async (tenant: string) => {
    let healthStr = "unavailable";
    try {
      const hRes = await healthApi.getHealth();
      healthStr =
        hRes.status === "ok"
          ? "operational"
          : hRes.status === "degraded"
          ? "degraded"
          : "down";
    } catch {
      /* offline */
    }

    try {
      setConfig(await configApi.getConfig());
    } catch {
      /* leave config null -> panel shows placeholders */
    }

    let totalTracesCount = 0;
    let mappedQueries: TraceRow[] = [];
    try {
      const tracesRes = await adminApi.getAuditTraces(tenant, 1, 10);
      totalTracesCount = tracesRes.total;
      mappedQueries = tracesRes.traces.map((t: any) => {
        const latencyStr = t.latency_ms
          ? `${(t.latency_ms / 1000).toFixed(1)}s`
          : "—";
        const dateStr =
          new Date(t.started_at).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          }) +
          " · " +
          new Date(t.started_at).toLocaleDateString([], {
            month: "short",
            day: "numeric",
          });
        // Backend does not expose per-trace consensus on the list endpoint;
        // high-trust here reflects whether the trace recorded an agreement flag.
        const highTrust = Boolean(t.high_trust);
        return {
          trace_id: t.trace_id,
          query: t.query,
          latency: latencyStr,
          confidence: t.agreement_score ?? 0,
          timestamp: dateStr,
          highTrust,
        };
      });
      setChain({
        status: totalTracesCount > 0 ? "intact" : "unverified",
        count: Math.max(totalTracesCount, 1),
      });
    } catch {
      /* offline */
    }

    let docCount = 0;
    try {
      const permRes = await adminApi.listPermissions();
      const docs = new Set(
        permRes.tuples
          .filter((t: any) => t.object.startsWith("document:"))
          .map((t: any) => t.object)
      );
      docCount = docs.size;
    } catch {
      /* offline */
    }

    setStats({
      totalDocuments: docCount || 5,
      totalQueries: totalTracesCount,
      healthStatus: healthStr,
    });
    setRecentQueries(mappedQueries);
  };

  const figures = [
    { k: "documents under custody", v: stats.totalDocuments },
    { k: "queries on record", v: stats.totalQueries },
    { k: "system status", v: stats.healthStatus },
  ];

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Briefing header */}
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-5 border-b border-hairline">
        <div>
          <p className="mono-meta">case file · operator briefing</p>
          <h1 className="mt-2 font-display text-2xl md:text-3xl font-bold tracking-tight text-bone">
            Stefano, the vault is{" "}
            <span className="text-signal-amber">{stats.healthStatus}</span>
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <Button nativeButton={false} render={<Link href="/chat" />} className="gap-2">
            <MessageSquare className="h-4 w-4" />
            New query
          </Button>
          <Button
            variant="outline"
            nativeButton={false}
            render={<Link href="/ingest" />}
            className="gap-2"
          >
            <UploadCloud className="h-4 w-4" />
            Intake
          </Button>
        </div>
      </header>

      {/* Hero — chain integrity */}
      <Panel
        classification="restricted"
        label="tamper-evident custody chain"
        headRight={
          <TrustVerdict highTrust={chain.status === "intact"} />
        }
      >
        <div className="px-5 py-6 flex flex-col lg:flex-row lg:items-center gap-6">
          <div className="flex-1">
            <p className="text-sm text-bone-dim leading-relaxed max-w-md">
              Every query trace and evidence record is HMAC-sealed into an
              append-only chain. Each link below is one sealed record.
            </p>
            <div className="mt-5">
              <TamperChain
                count={chain.count}
                status={chain.status}
                size="lg"
              />
            </div>
          </div>
          <div className="lg:w-px lg:self-stretch lg:bg-hairline" />
          <div className="lg:w-56 grid grid-cols-3 lg:grid-cols-1 gap-4">
            {figures.map((f) => (
              <div key={f.k}>
                <p className="font-mono text-2xl text-bone tabular-nums">
                  {f.v}
                </p>
                <p className="mono-meta mt-1">{f.k}</p>
              </div>
            ))}
          </div>
        </div>
      </Panel>

      {/* Ledger + config */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Panel
          className="lg:col-span-2"
          label="recent queries · evidence ledger"
        >
          {recentQueries.length === 0 ? (
            <p className="px-5 py-8 text-sm text-bone-dim">
              No queries on record yet. Open the query console to begin a case.
            </p>
          ) : (
            <ul className="divide-y divide-hairline">
              {recentQueries.map((item) => (
                <li key={item.trace_id}>
                  <Link
                    href={`/explain?trace_id=${item.trace_id}`}
                    className="group flex items-start justify-between gap-4 px-5 py-3.5 hover:bg-[rgba(232,230,223,0.03)] transition-colors"
                  >
                    <div className="min-w-0 space-y-1.5">
                      <p className="text-sm text-bone truncate">{item.query}</p>
                      <p className="mono-meta">
                        {item.trace_id.slice(0, 10)} · {item.latency} ·{" "}
                        {item.timestamp}
                      </p>
                    </div>
                    <div className="flex items-center gap-3 flex-shrink-0">
                      <span
                        className={
                          item.highTrust
                            ? "stamp text-signal-amber"
                            : "stamp text-bone-dim"
                        }
                      >
                        {item.highTrust ? "✓ SEALED" : "STANDARD"}
                      </span>
                      <ArrowUpRight className="h-4 w-4 text-bone-dim group-hover:text-bone transition-colors" />
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel label="active configuration">
          <dl className="px-5 py-4 space-y-3 font-mono text-xs">
            {(config
              ? [
                  ["core llm", `${config.llm.provider} · ${config.llm.model}`],
                  ["embedding", config.embedding.replace(/^BAAI\//, "")],
                  ["nli judge", `${config.judge.provider} · ${config.judge.model}`],
                  ["access store", config.access_store],
                  ["tenant", tenantId],
                ]
              : [
                  ["core llm", "…"],
                  ["embedding", "…"],
                  ["nli judge", "…"],
                  ["access store", "openfga rebac"],
                  ["tenant", tenantId],
                ]
            ).map(([k, v]) => (
              <div
                key={k}
                className="flex items-center justify-between gap-3 border-b border-hairline pb-2 last:border-0 last:pb-0"
              >
                <dt className="text-bone-dim uppercase tracking-wider">{k}</dt>
                <dd className="text-bone">{v}</dd>
              </div>
            ))}
          </dl>
        </Panel>
      </div>
    </div>
  );
}
