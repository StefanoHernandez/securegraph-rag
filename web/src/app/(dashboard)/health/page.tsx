"use client";

import React, { useState, useEffect, useCallback } from "react";
import { healthApi } from "@/lib/api";
import { HealthResponse, ServiceStatus } from "@/lib/types";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RefreshCw, CheckCircle, AlertTriangle, XCircle, Info, Activity, Clock } from "lucide-react";

export default function HealthPage() {
  const [data, setData] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const fetchHealth = useCallback(async () => {
    setLoading(true);
    try {
      const response = await healthApi.getHealth();
      setData(response);
      setError(null);
      setLastUpdated(new Date());
    } catch (err: any) {
      setError(err.message || "Failed to fetch system health.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchHealth();
  }, [fetchHealth]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      fetchHealth();
    }, 10000); // 10 seconds auto-refresh

    return () => clearInterval(interval);
  }, [autoRefresh, fetchHealth]);

  const getOverallStatusIcon = (status: string) => {
    switch (status) {
      case "ok":
        return <CheckCircle className="h-10 w-10 text-accent-emerald animate-pulse" />;
      case "degraded":
        return <AlertTriangle className="h-10 w-10 text-accent-amber" />;
      default:
        return <XCircle className="h-10 w-10 text-accent-rose" />;
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "ok":
        return <Badge className="bg-accent-emerald/10 text-accent-emerald border-accent-emerald/20 hover:bg-accent-emerald/20 text-xs">Healthy</Badge>;
      case "degraded":
        return <Badge className="bg-accent-amber/10 text-accent-amber border-accent-amber/20 hover:bg-accent-amber/20 text-xs">Degraded</Badge>;
      case "unavailable":
        return <Badge className="bg-accent-rose/10 text-accent-rose border-accent-rose/20 hover:bg-accent-rose/20 text-xs">Unavailable</Badge>;
      default:
        return <Badge variant="outline" className="text-foreground/50 border-foreground/10 text-xs">Not Configured</Badge>;
    }
  };

  const getServiceLabel = (key: string) => {
    switch (key) {
      case "postgres":
        return "PostgreSQL (Metadata Store)";
      case "qdrant":
        return "Qdrant (Vector Database)";
      case "neo4j":
        return "Neo4j (Knowledge Graph)";
      case "openfga":
        return "OpenFGA (Access Control Model)";
      case "ollama":
        return "Ollama (In-Host LLM Engine)";
      case "redis":
        return "Redis (Asynchronous Tasks Queue)";
      default:
        return key.toUpperCase();
    }
  };

  const getServiceIcon = (key: string) => {
    // Return standard representation or colors
    switch (key) {
      case "postgres":
        return "🐘";
      case "qdrant":
        return "🎯";
      case "neo4j":
        return "🕸️";
      case "openfga":
        return "🔑";
      case "ollama":
        return "🦙";
      case "redis":
        return "📦";
      default:
        return "⚙️";
    }
  };

  return (
    <div className="space-y-8 max-w-6xl mx-auto">
      {/* Top Banner Control */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-foreground">Infrastructure Monitoring</h2>
          <p className="text-xs text-foreground/55 font-mono">Real-time status of all active Graph-RAG services.</p>
        </div>
        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`border-border/50 text-xs h-9 bg-black/20 ${autoRefresh ? "text-accent-cyan border-accent-cyan/30" : "text-foreground/50"}`}
          >
            <Activity className="h-3.5 w-3.5 mr-1.5" />
            {autoRefresh ? "Auto-Refresh On" : "Auto-Refresh Off"}
          </Button>
          <Button
            size="sm"
            onClick={fetchHealth}
            disabled={loading}
            className="bg-accent-indigo text-black font-semibold hover:opacity-90 transition h-9 text-xs"
          >
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? "animate-spin" : ""}`} />
            Force Refresh
          </Button>
        </div>
      </div>

      {/* Error display */}
      {error && (
        <div className="p-4 rounded-xl bg-accent-rose/10 border border-accent-rose/20 text-accent-rose text-sm flex items-start gap-3">
          <XCircle className="h-5 w-5 mt-0.5 flex-shrink-0" />
          <div>
            <p className="font-semibold">Failed to refresh health statuses</p>
            <p className="text-xs text-accent-rose/80 mt-1">{error}</p>
          </div>
        </div>
      )}

      {/* System Status Summary */}
      {data && (
        <Card className="glassmorphism border-border/50 overflow-hidden relative">
          <CardContent className="p-6 md:p-8 flex flex-col md:flex-row items-center md:justify-between gap-6">
            <div className="flex items-center gap-5 text-center md:text-left flex-col md:flex-row">
              <div className="p-3 bg-white/5 rounded-2xl border border-white/10">
                {getOverallStatusIcon(data.status)}
              </div>
              <div className="space-y-1">
                <div className="flex items-center gap-2.5 justify-center md:justify-start">
                  <h3 className="text-2xl font-black tracking-tight text-foreground uppercase">
                    System {data.status}
                  </h3>
                </div>
                <p className="text-sm text-foreground/60 leading-relaxed max-w-lg">
                  {data.status === "ok"
                    ? "All backend systems are responding normally. Verifiable access controls and consensus verification are fully operational."
                    : data.status === "degraded"
                    ? "One or more background services are degraded or slow. Queries may have increased latency or limited context channels."
                    : "Major infrastructure disruption. Core databases or security controllers are offline. Services are locked down."}
                </p>
              </div>
            </div>
            
            <div className="flex md:flex-col items-center md:items-end gap-2 text-xs font-mono text-foreground/45 border-t md:border-t-0 md:border-l border-border/30 pt-4 md:pt-0 md:pl-6 w-full md:w-auto justify-between">
              <div className="flex items-center gap-1.5">
                <Info className="h-3.5 w-3.5 text-accent-cyan" />
                <span>Env: <b className="text-foreground">{data.env}</b></span>
              </div>
              <div className="flex items-center gap-1.5">
                <Clock className="h-3.5 w-3.5" />
                <span>Last check: <b className="text-foreground">{lastUpdated?.toLocaleTimeString()}</b></span>
              </div>
              <div className="text-[10px]">
                SecurGraph version: {data.version}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Grid of Components */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {data &&
          Object.entries(data.components).map(([key, service]) => (
            <Card
              key={key}
              className="glassmorphism border-border/40 hover:border-accent-indigo/20 transition-all duration-300 relative overflow-hidden"
            >
              {/* Status accent side bar */}
              <div
                className={`absolute left-0 top-0 bottom-0 w-[4px] ${
                  service.status === "ok"
                    ? "bg-accent-emerald"
                    : service.status === "degraded"
                    ? "bg-accent-amber"
                    : "bg-accent-rose"
                }`}
              />

              <CardHeader className="pb-3 pl-6">
                <div className="flex items-center justify-between">
                  <span className="text-2xl select-none">{getServiceIcon(key)}</span>
                  {getStatusBadge(service.status)}
                </div>
                <CardTitle className="text-sm font-bold text-foreground pt-2">
                  {getServiceLabel(key)}
                </CardTitle>
                <CardDescription className="text-[10px] uppercase font-mono tracking-wider text-foreground/40">
                  service_key: {key}
                </CardDescription>
              </CardHeader>

              <CardContent className="pl-6 pb-6 space-y-4">
                {/* Latency display */}
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-foreground/50">Ping Latency</span>
                  {service.latency_ms !== null ? (
                    <span className="text-foreground font-semibold flex items-center gap-1.5">
                      {service.latency_ms.toFixed(1)} ms
                      <span className="h-1.5 w-1.5 rounded-full bg-accent-cyan animate-ping" />
                    </span>
                  ) : (
                    <span className="text-foreground/30">—</span>
                  )}
                </div>

                {/* Status description */}
                {service.detail && (
                  <div className="p-2.5 rounded bg-black/40 border border-border/50 text-[11px] font-mono text-accent-rose/90 overflow-x-auto max-h-24 custom-scrollbar">
                    {service.detail}
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
      </div>
    </div>
  );
}
