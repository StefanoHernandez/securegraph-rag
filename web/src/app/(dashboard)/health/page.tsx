"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useTranslations } from "next-intl";
import { healthApi } from "@/lib/api";
import { HealthResponse } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/forensic";
import { RefreshCw, Activity } from "lucide-react";
import { getErrorMessage } from "@/lib/errors";

export default function HealthPage() {
  const t = useTranslations("healthPage");
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
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("loadError")));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    const timer = window.setTimeout(() => void fetchHealth(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchHealth]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      fetchHealth();
    }, 60000);

    return () => clearInterval(interval);
  }, [autoRefresh, fetchHealth]);

  const getServiceLabel = (key: string) => {
    switch (key) {
      case "postgres":
        return t("postgres");
      case "qdrant":
        return t("qdrant");
      case "neo4j":
        return t("neo4j");
      case "openfga":
        return t("openfga");
      case "provider":
        return t("providerService");
      case "ollama":
        return t("ollama");
      case "redis":
        return t("redis");
      default:
        return key.toUpperCase();
    }
  };

  const statusStamp = (status: string) => {
    switch (status) {
      case "ok":
        return <span className="stamp text-signal-amber">{t("statusOk")}</span>;
      case "degraded":
        return <span className="stamp text-bone-dim">{t("statusDegraded")}</span>;
      case "unavailable":
        return <span className="stamp text-seal-red">{t("statusDown")}</span>;
      default:
        return <span className="stamp text-bone-dim">{t("statusUnknown")}</span>;
    }
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6 pb-10">
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-4 border-b border-hairline">
        <div>
          <p className="mono-meta">{t("eyebrow")}</p>
          <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-bone">
            {t("title")}
          </h1>
        </div>
        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`gap-1.5 ${autoRefresh ? "text-signal-amber border-signal-amber" : ""}`}
          >
            <Activity className="h-3.5 w-3.5" />
            {autoRefresh ? t("autoOn") : t("autoOff")}
          </Button>
          <Button size="sm" onClick={fetchHealth} disabled={loading} className="gap-1.5">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            {t("refresh")}
          </Button>
        </div>
      </header>

      {error && (
        <Panel classification="restricted" label={t("error")}>
          <p className="px-5 py-4 font-mono text-xs text-seal-red">{error}</p>
        </Panel>
      )}

      {data && (
        <Panel
          classification={data.status === "ok" ? "internal" : "restricted"}
          label={t("overallStatus")}
          headRight={statusStamp(data.status)}
        >
          <div className="px-5 py-6 flex flex-col md:flex-row md:items-center md:justify-between gap-6">
            <div className="space-y-2 max-w-lg">
              <h3 className="font-display text-xl font-bold tracking-tight text-bone uppercase">
                {t("system")}{" "}
                <span
                  className={
                    data.status === "ok" ? "text-signal-amber" : "text-seal-red"
                  }
                >
                  {data.status}
                </span>
              </h3>
              <p className="text-sm text-bone-dim leading-relaxed">
                {data.status === "ok"
                  ? t("okText")
                  : data.status === "degraded"
                    ? t("degradedText")
                    : t("downText")}
              </p>
            </div>

            <dl className="grid grid-cols-1 gap-2 font-mono text-xs border-t md:border-t-0 md:border-l border-hairline pt-4 md:pt-0 md:pl-6">
              <div className="flex items-center justify-between gap-6">
                <dt className="mono-meta normal-case">{t("env")}</dt>
                <dd className="text-bone">{data.env}</dd>
              </div>
              <div className="flex items-center justify-between gap-6">
                <dt className="mono-meta normal-case">{t("lastCheck")}</dt>
                <dd className="text-bone">{lastUpdated?.toLocaleTimeString()}</dd>
              </div>
              <div className="flex items-center justify-between gap-6">
                <dt className="mono-meta normal-case">{t("version")}</dt>
                <dd className="text-bone">{data.version}</dd>
              </div>
            </dl>
          </div>
        </Panel>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-px bg-hairline border border-hairline">
        {data &&
          Object.entries(data.components).map(([key, service]) => (
            <div
              key={key}
              className="bg-vault-raised classification-gutter px-5 py-4 space-y-3"
              style={{
                ["--gutter-color" as string]:
                  service.status === "ok"
                    ? "var(--signal-amber)"
                    : service.status === "degraded"
                    ? "var(--bone-dim)"
                    : "var(--seal-red)",
              }}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-display text-sm font-bold text-bone">
                    {getServiceLabel(key)}
                  </p>
                  <p className="mono-meta mt-1 normal-case">{t("serviceKey", { key })}</p>
                </div>
                {statusStamp(service.status)}
              </div>

              <div className="flex items-center justify-between border-t border-hairline pt-3 font-mono text-xs">
                <span className="mono-meta normal-case">{t("pingLatency")}</span>
                {service.latency_ms !== null ? (
                  <span className="text-bone tabular-nums">
                    {service.latency_ms.toFixed(1)} ms
                  </span>
                ) : (
                  <span className="text-bone-dim">—</span>
                )}
              </div>

              {service.detail && (
                <div className="bg-vault-sunk border border-hairline px-2.5 py-2 font-mono text-[0.625rem] text-seal-red overflow-x-auto max-h-24 custom-scrollbar">
                  {service.detail}
                </div>
              )}
            </div>
          ))}
      </div>
    </div>
  );
}
