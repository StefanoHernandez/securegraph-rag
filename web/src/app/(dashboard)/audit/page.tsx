"use client";

import React, { useState, useEffect } from "react";
import { 
  FileCode, 
  ShieldCheck, 
  ShieldAlert, 
  Database, 
  Search, 
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  Clock,
  User,
  Activity,
  Trash2,
  KeyRound,
  Fingerprint
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { adminApi } from "@/lib/api";
import Link from "next/link";

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
    {
      is_intact: boolean;
      tampered_count: number;
      tampered_ids: string[];
    }
  >;
}

export default function AuditPage() {
  const [tenantId, setTenantId] = useState("default");
  const [traces, setTraces] = useState<AuditTrace[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(10);
  
  const [loadingTraces, setLoadingTraces] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [verifReport, setVerifReport] = useState<VerificationReport | null>(null);
  const [verifError, setVerifError] = useState<string | null>(null);

  useEffect(() => {
    // Read tenant ID from logged in session
    const storedTenant = localStorage.getItem("securgraph_tenant");
    if (storedTenant) {
      setTenantId(storedTenant);
    }
  }, []);

  // Fetch traces when tenant_id or page changes
  useEffect(() => {
    fetchTraces();
  }, [tenantId, page]);

  const fetchTraces = async () => {
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
  };

  const handleVerifyChain = async () => {
    setVerifying(true);
    setVerifError(null);
    setVerifReport(null);
    try {
      const report = await adminApi.verifyAuditChain(tenantId);
      setVerifReport(report);
    } catch (err: any) {
      console.error(err);
      setVerifError(err?.message || "Errore nella verifica crittografica delle tabelle.");
    } finally {
      setVerifying(false);
    }
  };

  const formatTimestamp = (isoString: string) => {
    try {
      const date = new Date(isoString);
      return date.toLocaleString("it-IT", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch (e) {
      return isoString;
    }
  };

  return (
    <div className="space-y-8 max-w-7xl mx-auto pb-10">
      
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-border/20 pb-4">
        <div>
          <h1 className="text-xl md:text-2xl font-black text-foreground">
            Audit Ledger & Cryptographic Ledger
          </h1>
          <p className="text-[10px] text-foreground/45 font-mono">
            Verifiable logs secured via HMAC-SHA256 hash chains.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-foreground/50 font-mono">Scope Tenant:</span>
          <select
            value={tenantId}
            onChange={(e) => {
              const val = e.target.value;
              setTenantId(val);
              localStorage.setItem("securgraph_tenant", val);
              window.location.reload();
            }}
            className="bg-black/45 border border-border/40 rounded-lg px-2.5 py-1.5 text-xs text-foreground font-mono focus:outline-none focus:border-accent-indigo"
          >
            <option value="default">default</option>
            <option value="mrd">mrd</option>
            <option value="demo_tenant">demo_tenant</option>
            <option value="test_tenant_async">test_tenant_async</option>
          </select>
          <Button
            size="sm"
            onClick={fetchTraces}
            variant="outline"
            className="border-border/40 hover:bg-white/5 cursor-pointer h-8 text-xs font-bold"
            disabled={loadingTraces}
          >
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loadingTraces ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Main layout grid (Left: Verification Panel, Right: Logs Table) */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 items-start">
        
        {/* Verification Control Panel (1 Column) */}
        <div className="lg:col-span-1 space-y-6">
          <Card className="glassmorphism border-border/50 text-foreground">
            <CardHeader className="pb-3 border-b border-border/10">
              <CardTitle className="text-sm font-bold flex items-center gap-1.5">
                <Fingerprint className="h-4.5 w-4.5 text-accent-indigo" />
                Chain Verification
              </CardTitle>
              <CardDescription className="text-[10px]">
                Validates query trace, evidence retrieval and policy decision HMAC signatures.
              </CardDescription>
            </CardHeader>
            <CardContent className="p-4 space-y-4 text-xs">
              <p className="text-[11px] text-foreground/60 leading-relaxed font-sans">
                L'audit log di SecurGraph utilizza una catena crittografica sequenziale dove ogni record include un HMAC generato usando una chiave privata salvata solo in memoria nel server.
              </p>
              
              <Button
                onClick={handleVerifyChain}
                className="w-full bg-accent-indigo hover:opacity-95 text-black font-bold text-xs h-9 cursor-pointer"
                disabled={verifying}
              >
                {verifying ? (
                  <div className="flex items-center gap-2">
                    <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                    Verifica in corso...
                  </div>
                ) : (
                  <div className="flex items-center gap-1.5">
                    <KeyRound className="h-3.5 w-3.5" />
                    Verify Database Chain
                  </div>
                )}
              </Button>

              {/* Verification Results Output */}
              {verifError && (
                <div className="p-3 bg-accent-rose/10 border border-accent-rose/25 text-accent-rose rounded-lg text-[10px] font-mono leading-normal">
                  <b>Errore:</b> {verifError}
                </div>
              )}

              {verifReport && (
                <div className="space-y-4 pt-2">
                  
                  {/* Status Banner */}
                  {verifReport.is_intact ? (
                    <div className="p-3 bg-accent-emerald/10 border border-accent-emerald/25 text-accent-emerald rounded-lg text-center flex flex-col items-center gap-1">
                      <ShieldCheck className="h-6 w-6" />
                      <span className="font-bold text-[10px] font-mono uppercase tracking-wider">Ledger Secure</span>
                      <p className="text-[9px] text-foreground/50 leading-normal font-sans">Tutti i record sono integri. Nessuna alterazione rilevata.</p>
                    </div>
                  ) : (
                    <div className="p-3 bg-accent-rose/10 border border-accent-rose/25 text-accent-rose rounded-lg text-center flex flex-col items-center gap-1">
                      <ShieldAlert className="h-6 w-6 animate-bounce" />
                      <span className="font-bold text-[10px] font-mono uppercase tracking-wider">Tamper Detected</span>
                      <p className="text-[9px] text-foreground/60 leading-normal font-sans">Rilevata una discrepanza nella firma di uno o più record.</p>
                    </div>
                  )}

                  {/* Individual Tables Audit */}
                  <div className="space-y-2 font-mono text-[9px] border-t border-border/10 pt-3">
                    
                    {/* query_traces */}
                    <div className="flex items-center justify-between border-b border-border/5 pb-1">
                      <span className="text-foreground/50">Query Traces</span>
                      <Badge className={`h-4.5 text-[8px] font-bold ${
                        verifReport.tables.query_traces.is_intact 
                          ? "bg-accent-emerald/10 text-accent-emerald hover:bg-accent-emerald/10" 
                          : "bg-accent-rose/10 text-accent-rose hover:bg-accent-rose/10"
                      }`}>
                        {verifReport.tables.query_traces.is_intact ? "INTACT" : `TAMPERED (${verifReport.tables.query_traces.tampered_count})`}
                      </Badge>
                    </div>

                    {/* retrieval_evidence */}
                    <div className="flex items-center justify-between border-b border-border/5 pb-1">
                      <span className="text-foreground/50">Retrieval Evidence</span>
                      <Badge className={`h-4.5 text-[8px] font-bold ${
                        verifReport.tables.retrieval_evidence.is_intact 
                          ? "bg-accent-emerald/10 text-accent-emerald hover:bg-accent-emerald/10" 
                          : "bg-accent-rose/10 text-accent-rose hover:bg-accent-rose/10"
                      }`}>
                        {verifReport.tables.retrieval_evidence.is_intact ? "INTACT" : `TAMPERED (${verifReport.tables.retrieval_evidence.tampered_count})`}
                      </Badge>
                    </div>

                    {/* policy_decisions */}
                    <div className="flex items-center justify-between">
                      <span className="text-foreground/50">Policy Decisions</span>
                      <Badge className={`h-4.5 text-[8px] font-bold ${
                        verifReport.tables.policy_decisions.is_intact 
                          ? "bg-accent-emerald/10 text-accent-emerald hover:bg-accent-emerald/10" 
                          : "bg-accent-rose/10 text-accent-rose hover:bg-accent-rose/10"
                      }`}>
                        {verifReport.tables.policy_decisions.is_intact ? "INTACT" : `TAMPERED (${verifReport.tables.policy_decisions.tampered_count})`}
                      </Badge>
                    </div>

                    {/* Display exact tampered IDs */}
                    {!verifReport.is_intact && (
                      <div className="mt-3 p-2 bg-black/40 border border-border/20 rounded-md text-[8px] max-h-24 overflow-y-auto custom-scrollbar space-y-1">
                        <span className="text-accent-rose font-bold uppercase">Compromised IDs:</span>
                        {Object.entries(verifReport.tables).map(([tableName, summary]) => 
                          summary.tampered_ids.map(id => (
                            <div key={id} className="text-foreground/60 truncate" title={`${tableName}: ${id}`}>
                              {tableName}: <b>{id}</b>
                            </div>
                          ))
                        )}
                      </div>
                    )}

                  </div>

                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Audit Log Table (3 Columns) */}
        <Card className="lg:col-span-3 glassmorphism border-border/50 text-foreground min-h-[500px] flex flex-col justify-between">
          <div>
            <CardHeader className="pb-3 border-b border-border/10">
              <CardTitle className="text-sm font-bold flex items-center gap-1.5">
                <Activity className="h-4.5 w-4.5 text-accent-cyan" />
                Query Execution Audit Log ({total} traces)
              </CardTitle>
              <CardDescription className="text-xs">
                History of all secure Graph-RAG queries evaluated under the active OpenFGA policies.
              </CardDescription>
            </CardHeader>
            
            <div className="overflow-x-auto custom-scrollbar">
              {loadingTraces ? (
                <div className="py-24 text-center text-xs text-foreground/45 flex flex-col items-center justify-center gap-2">
                  <RefreshCw className="h-6 w-6 animate-spin text-accent-indigo" />
                  <span>Caricamento dei log di audit...</span>
                </div>
              ) : traces.length === 0 ? (
                <div className="py-24 text-center text-xs text-foreground/45 flex flex-col items-center justify-center gap-2">
                  <FileCode className="h-8 w-8 text-foreground/20" />
                  <span>Nessun log di audit trovato per il tenant {tenantId}.</span>
                </div>
              ) : (
                <table className="w-full text-left text-xs font-mono border-collapse min-w-[700px]">
                  <thead>
                    <tr className="bg-black/30 border-b border-border/10 text-foreground/50 text-[10px] uppercase font-bold tracking-wider">
                      <th className="p-4">Timestamp</th>
                      <th className="p-4">User</th>
                      <th className="p-4">User Query</th>
                      <th className="p-4">Latency</th>
                      <th className="p-4">Tamper Signature</th>
                      <th className="p-4 text-center">Verify</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/10">
                    {traces.map((trace) => (
                      <tr key={trace.trace_id} className="hover:bg-white/5 transition">
                        <td className="p-4 whitespace-nowrap text-foreground/75">
                          {formatTimestamp(trace.started_at)}
                        </td>
                        <td className="p-4 font-bold text-accent-emerald flex items-center gap-1 mt-1">
                          <User className="h-3 w-3" />
                          {trace.user_id}
                        </td>
                        <td className="p-4 text-foreground/90 font-sans max-w-xs truncate" title={trace.query}>
                          {trace.query}
                        </td>
                        <td className="p-4 text-accent-cyan whitespace-nowrap">
                          {trace.latency_ms ? `${(trace.latency_ms / 1000).toFixed(2)}s` : "N/A"}
                        </td>
                        <td className="p-4 font-mono text-[10px] text-foreground/40 max-w-[150px] truncate" title={trace.tamper_hash || ""}>
                          {trace.tamper_hash || "genesis_trace"}
                        </td>
                        <td className="p-4 text-center">
                          <Link href={`/explain?trace_id=${trace.trace_id}`}>
                            <Button size="sm" variant="ghost" className="h-7 px-2 hover:bg-accent-indigo/15 text-accent-indigo rounded-md text-[10px] cursor-pointer">
                              Trace
                              <ChevronRight className="h-3.5 w-3.5 ml-0.5" />
                            </Button>
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {/* Pagination Controls */}
          {total > size && (
            <div className="p-4 border-t border-border/10 bg-black/20 flex items-center justify-between text-xs font-mono">
              <span className="text-foreground/45">
                Mostrati <b>{traces.length}</b> di <b>{total}</b> log
              </span>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  className="border-border/40 h-8 text-[10px] hover:bg-white/5 cursor-pointer"
                  disabled={page === 1}
                  onClick={() => setPage(prev => Math.max(1, prev - 1))}
                >
                  <ChevronLeft className="h-4 w-4 mr-0.5" /> Precedente
                </Button>
                <span className="text-foreground/75 px-2">Pagina {page} di {Math.ceil(total / size)}</span>
                <Button
                  size="sm"
                  variant="outline"
                  className="border-border/40 h-8 text-[10px] hover:bg-white/5 cursor-pointer"
                  disabled={page >= Math.ceil(total / size)}
                  onClick={() => setPage(prev => prev + 1)}
                >
                  Successivo <ChevronRight className="h-4 w-4 ml-0.5" />
                </Button>
              </div>
            </div>
          )}

        </Card>
      </div>

    </div>
  );
}
