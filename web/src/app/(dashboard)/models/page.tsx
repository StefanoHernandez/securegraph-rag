"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/forensic";
import { getErrorMessage } from "@/lib/errors";
import { settingsApi } from "@/lib/api";
import { ModelCatalogEntry, ProviderName } from "@/lib/types";
import { useTenantSelection } from "@/lib/useTenantSelection";

const PROVIDERS: Array<ProviderName | "all"> = [
  "all",
  "openai",
  "anthropic",
  "gemini",
  "mistral",
  "ollama",
];

export default function ModelsPage() {
  const { tenantId, loadingTenants } = useTenantSelection();
  const [models, setModels] = useState<ModelCatalogEntry[]>([]);
  const [providerFilter, setProviderFilter] = useState<ProviderName | "all">("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadModels = useCallback(async () => {
    if (!tenantId) {
      setLoading(loadingTenants);
      return;
    }
    setLoading(true);
    try {
      setModels(await settingsApi.listModels(tenantId));
      setError(null);
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Unable to load model catalog."));
    } finally {
      setLoading(false);
    }
  }, [loadingTenants, tenantId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadModels(), 0);
    return () => window.clearTimeout(timer);
  }, [loadModels]);

  const visibleModels = useMemo(
    () =>
      providerFilter === "all"
        ? models
        : models.filter((model) => model.provider === providerFilter),
    [models, providerFilter]
  );

  return (
    <div className="max-w-6xl mx-auto space-y-6 pb-10">
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-4 border-b border-hairline">
        <div>
          <p className="mono-meta">section 09 · provider model catalog</p>
          <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-bone">
            Models
          </h1>
        </div>
        <div className="flex items-center gap-3">
          <select
            value={providerFilter}
            onChange={(event) =>
              setProviderFilter(event.target.value as ProviderName | "all")
            }
            className="h-9 bg-vault-raised border border-hairline px-3 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
          >
            {PROVIDERS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            onClick={loadModels}
            disabled={loading || loadingTenants || !tenantId}
            className="gap-2"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </header>

      {error && (
        <Panel classification="restricted" label="model catalog error">
          <p className="px-5 py-4 font-mono text-xs text-seal-red">{error}</p>
        </Panel>
      )}

      <Panel label="catalog entries">
        {visibleModels.length === 0 ? (
          <div className="px-5 py-8 text-sm text-bone-dim">
            {loading ? "Loading model catalog." : "No models recorded for this tenant."}
          </div>
        ) : (
          <div className="overflow-x-auto custom-scrollbar">
            <table className="w-full text-left">
              <thead className="border-b border-hairline">
                <tr className="mono-meta">
                  <th className="px-4 py-2 font-normal">provider</th>
                  <th className="px-4 py-2 font-normal">model</th>
                  <th className="px-4 py-2 font-normal">capabilities</th>
                  <th className="px-4 py-2 font-normal">status</th>
                  <th className="px-4 py-2 font-normal">discovered</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                {visibleModels.map((model) => (
                  <tr key={`${model.provider}:${model.model_id}`}>
                    <td className="px-4 py-3 font-mono text-xs text-bone">
                      {model.provider}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-bone">
                      {model.model_id}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-bone-dim">
                      {model.capabilities.join(", ") || "—"}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-bone">
                      {model.status}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-bone-dim">
                      {model.discovered_at
                        ? new Date(model.discovered_at).toLocaleString()
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
