"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  KeyRound,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Panel, TamperChain, type ChainStatus } from "@/components/forensic";
import { adminApi } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { useTenantSelection } from "@/lib/useTenantSelection";

interface AuditTrace {
  trace_id: string;
  tenant_id: string;
  user_id: string;
  query: string;
  answer: string | null;
  started_at: string;
  completed_at: string | null;
  latency_ms: number | null;
  tamper_hash: string | null;
}

interface VerificationReport {
  tenant_id: string;
  is_intact: boolean;
  tables: Record<
    string,
    { is_intact: boolean; tampered_count: number; tampered_ids: string[] }
  >;
}

const TABLE_LABELS: Record<string, string> = {
  query_traces: "query traces",
  retrieval_evidence: "retrieval evidence",
  policy_decisions: "policy decisions",
};

export default function AuditPage() {
  const { tenants, tenantId, loadingTenants, tenantError, selectTenant } =
    useTenantSelection();
  const [traces, setTraces] = useState<AuditTrace[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const size = 10;

  const [loadingTraces, setLoadingTraces] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [verifReport, setVerifReport] = useState<VerificationReport | null>(null);
  const [verifError, setVerifError] = useState<string | null>(null);

  const fetchTraces = useCallback(async () => {
    if (!tenantId) {
      return;
    }
    setLoadingTraces(true);
    try {
      const res = await adminApi.getAuditTraces(tenantId, page, size);
      setTraces(res.traces);
      setTotal(res.total);
    } catch (err) {
      console.error("Failed to fetch audit traces", err);
    } finally {
      setLoadingTraces(false);
    }
  }, [page, tenantId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void fetchTraces(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchTraces]);

  const handleVerifyChain = async () => {
    if (!tenantId) {
      return;
    }
    setVerifying(true);
    setVerifError(null);
    setVerifReport(null);
    try {
      const report = await adminApi.verifyAuditChain(tenantId);
      setVerifReport(report);
    } catch (err: unknown) {
      setVerifError(getErrorMessage(err, "Chain verification failed."));
    } finally {
      setVerifying(false);
    }
  };

  const formatTimestamp = (iso: string) => {
    try {
      return new Date(iso).toLocaleString("en-GB", {
        year: "2-digit",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      return iso;
    }
  };

  // Derive hero chain state from the verification report.
  const chainStatus: ChainStatus = !verifReport
    ? "unverified"
    : verifReport.is_intact
    ? "intact"
    : "tampered";
  const totalTampered = verifReport
    ? Object.values(verifReport.tables).reduce(
        (a, t) => a + t.tampered_count,
        0
      )
    : 0;
  const chainCount = Math.max(total, 1);
  const brokenAt =
    chainStatus === "tampered"
      ? Math.max(Math.floor(chainCount / 2), 0)
      : undefined;

  return (
    <div className="max-w-7xl mx-auto space-y-6 pb-10">
      {/* Header */}
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-4 border-b border-hairline">
        <div>
          <p className="mono-meta">section 06 · tamper-evident</p>
          <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-bone">
            Chain of Custody
          </h1>
        </div>
        <div className="flex items-center gap-3">
          <select
            value={tenantId ?? ""}
            onChange={(e) => {
              selectTenant(e.target.value);
              setPage(1);
            }}
            disabled={loadingTenants || tenants.length === 0}
            className="bg-vault-raised border border-hairline px-2.5 py-1.5 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
          >
            {!tenantId && <option value="">no tenant</option>}
            {tenants.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>
                {tenant.display_name}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            variant="outline"
            onClick={fetchTraces}
            disabled={loadingTraces}
            className="gap-1.5"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loadingTraces ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </header>

      {tenantError && (
        <Panel classification="restricted" label="tenant directory error">
          <p className="px-5 py-4 font-mono text-xs text-seal-red">{tenantError}</p>
        </Panel>
      )}

      {/* Hero — interactive chain verification */}
      <Panel
        classification={chainStatus === "tampered" ? "restricted" : "internal"}
        label="hmac custody chain · verify integrity"
        headRight={
          <Button
            size="sm"
            onClick={handleVerifyChain}
            disabled={verifying}
            className="gap-1.5"
          >
            {verifying ? (
              <RefreshCw className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <KeyRound className="h-3.5 w-3.5" />
            )}
            {verifying ? "verifying…" : "verify chain"}
          </Button>
        }
      >
        <div className="px-5 py-6 space-y-5">
          <p className="text-sm text-bone-dim max-w-2xl leading-relaxed">
            Each record carries an HMAC-SHA256 seal computed over its payload and
            the previous link, keyed by a secret held only in server memory.
            Verification re-derives every seal; a single altered byte breaks the
            chain at that link.
          </p>

          <TamperChain
            count={chainCount}
            status={chainStatus}
            brokenAt={brokenAt}
            animate
            size="lg"
          />

          {verifError && (
            <p className="font-mono text-xs text-seal-red">⚠ {verifError}</p>
          )}

          {verifReport && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-px bg-hairline border border-hairline">
              {Object.entries(verifReport.tables).map(([name, t]) => (
                <div
                  key={name}
                  className="bg-vault-raised px-4 py-3 flex items-center justify-between"
                >
                  <span className="mono-meta normal-case">
                    {TABLE_LABELS[name] ?? name}
                  </span>
                  <span
                    className={
                      t.is_intact
                        ? "stamp text-signal-amber"
                        : "stamp text-seal-red"
                    }
                  >
                    {t.is_intact ? "✓ INTACT" : `BROKEN ${t.tampered_count}`}
                  </span>
                </div>
              ))}
            </div>
          )}

          {verifReport && !verifReport.is_intact && (
            <div className="border border-seal-red/40 bg-vault-sunk px-4 py-3 max-h-32 overflow-y-auto custom-scrollbar">
              <p className="mono-meta text-seal-red mb-1.5">
                compromised records · {totalTampered}
              </p>
              <ul className="space-y-0.5 font-mono text-[0.625rem] text-bone-dim">
                {Object.entries(verifReport.tables).flatMap(([name, t]) =>
                  t.tampered_ids.map((id) => (
                    <li key={`${name}:${id}`} className="truncate">
                      {TABLE_LABELS[name] ?? name}: <span className="text-bone">{id}</span>
                    </li>
                  ))
                )}
              </ul>
            </div>
          )}
        </div>
      </Panel>

      {/* Ledger */}
      <Panel label={`query execution ledger · ${total} records`}>
        <div className="overflow-x-auto custom-scrollbar">
          {loadingTraces ? (
            <div className="py-20 flex flex-col items-center gap-2 text-bone-dim">
              <RefreshCw className="h-5 w-5 animate-spin text-signal-amber" />
              <span className="mono-meta">loading ledger…</span>
            </div>
          ) : traces.length === 0 ? (
            <div className="py-20 text-center mono-meta">
              no records for tenant {tenantId ?? "—"}
            </div>
          ) : (
            <table className="w-full text-left text-xs border-collapse min-w-[720px]">
              <thead>
                <tr className="border-b border-hairline text-bone-dim">
                  {["timestamp", "operator", "query", "latency", "seal", ""].map(
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
                {traces.map((trace) => (
                  <tr
                    key={trace.trace_id}
                    className="hover:bg-[rgba(232,230,223,0.03)] transition-colors"
                  >
                    <td className="px-4 py-3 font-mono text-bone-dim whitespace-nowrap">
                      {formatTimestamp(trace.started_at)}
                    </td>
                    <td className="px-4 py-3 font-mono text-signal-amber whitespace-nowrap">
                      {trace.user_id}
                    </td>
                    <td className="px-4 py-3 text-bone max-w-xs truncate" title={trace.query}>
                      {trace.query}
                    </td>
                    <td className="px-4 py-3 font-mono text-bone-dim whitespace-nowrap">
                      {trace.latency_ms ? `${(trace.latency_ms / 1000).toFixed(2)}s` : "—"}
                    </td>
                    <td
                      className="px-4 py-3 font-mono text-[0.625rem] text-bone-dim max-w-[140px] truncate"
                      title={trace.tamper_hash || ""}
                    >
                      {trace.tamper_hash ? trace.tamper_hash.slice(0, 16) : "genesis"}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Link
                        href={`/explain?trace_id=${trace.trace_id}`}
                        className="inline-flex items-center gap-0.5 font-mono text-[0.625rem] uppercase tracking-wider text-bone-dim hover:text-signal-amber transition-colors"
                      >
                        trace <ChevronRight className="h-3.5 w-3.5" />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {total > size && (
          <div className="px-4 py-3 border-t border-hairline flex items-center justify-between mono-meta">
            <span>
              {traces.length} of {total}
            </span>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={page === 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span className="text-bone">
                {page} / {Math.ceil(total / size)}
              </span>
              <Button
                size="sm"
                variant="outline"
                disabled={page >= Math.ceil(total / size)}
                onClick={() => setPage((p) => p + 1)}
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
      </Panel>
    </div>
  );
}
