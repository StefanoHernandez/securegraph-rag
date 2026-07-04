"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Calculator, CheckCircle2, KeyRound, RefreshCw, Save, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel } from "@/components/forensic";
import { getErrorMessage } from "@/lib/errors";
import { settingsApi } from "@/lib/api";
import {
  ProviderCredentialStatus,
  ProviderName,
  RuntimeSetting,
  CostEstimateResponse,
  EgressClassification,
  EgressStage,
  EmbeddingReindexPlan,
} from "@/lib/types";
import { useTenantSelection } from "@/lib/useTenantSelection";

const PROVIDERS: ProviderName[] = ["openai", "anthropic", "gemini", "mistral", "ollama"];
const RUNTIME_KEYS = [
  "llm_provider",
  "llm_model",
  "llm_provider_extract",
  "llm_model_extract",
  "llm_provider_judge",
  "llm_model_judge",
  "fallback_policy",
  "hard_token_cap",
] as const;
const DEFAULT_EGRESS_POLICY = JSON.stringify(
  {
    default: "block",
    rules: [
      {
        provider: "ollama",
        stage: "any",
        max_classification: "restricted",
        action: "allow",
      },
      {
        provider: "openai",
        stage: "generation",
        max_classification: "internal",
        action: "allow",
      },
    ],
  },
  null,
  2
);

export default function SettingsPage() {
  const t = useTranslations("settingsPage");
  const { tenantId, loadingTenants } = useTenantSelection();
  const [providers, setProviders] = useState<ProviderCredentialStatus[]>([]);
  const [provider, setProvider] = useState<ProviderName>("openai");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [runtimeKey, setRuntimeKey] =
    useState<(typeof RUNTIME_KEYS)[number]>("llm_provider");
  const [runtimeValue, setRuntimeValue] = useState("openai");
  const [savingProvider, setSavingProvider] = useState(false);
  const [savingRuntime, setSavingRuntime] = useState(false);
  const [runtimeResult, setRuntimeResult] = useState<RuntimeSetting | null>(null);
  const [egressPolicy, setEgressPolicy] = useState(DEFAULT_EGRESS_POLICY);
  const [savingEgress, setSavingEgress] = useState(false);
  const [egressResult, setEgressResult] = useState<RuntimeSetting | null>(null);
  const [estimateProvider, setEstimateProvider] = useState<ProviderName>("openai");
  const [estimateModel, setEstimateModel] = useState("gpt-4.1-mini");
  const [estimateStage, setEstimateStage] = useState<EgressStage>("generation");
  const [estimateClassification, setEstimateClassification] =
    useState<EgressClassification>("internal");
  const [inputTokens, setInputTokens] = useState("1200");
  const [outputTokens, setOutputTokens] = useState("400");
  const [estimatingCost, setEstimatingCost] = useState(false);
  const [costEstimate, setCostEstimate] = useState<CostEstimateResponse | null>(null);
  const [reindexTargetModel, setReindexTargetModel] = useState("BAAI/bge-m3");
  const [reindexDim, setReindexDim] = useState("1024");
  const [planningReindex, setPlanningReindex] = useState(false);
  const [reindexPlan, setReindexPlan] = useState<EmbeddingReindexPlan | null>(null);
  const [error, setError] = useState<string | null>(null);

  const configured = useMemo(
    () => new Map(providers.map((item) => [item.provider, item])),
    [providers]
  );

  const loadProviders = useCallback(async () => {
    if (!tenantId) {
      return;
    }
    try {
      setProviders(await settingsApi.listProviders(tenantId));
      setError(null);
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("loadError")));
    }
  }, [tenantId, t]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadProviders(), 0);
    return () => window.clearTimeout(timer);
  }, [loadProviders]);

  const saveProvider = async (event: React.FormEvent) => {
    event.preventDefault();
    setSavingProvider(true);
    setError(null);
    try {
      if (!tenantId) {
        throw new Error(t("noTenant"));
      }
      const result = await settingsApi.saveProviderCredential(
        tenantId,
        provider,
        apiKey,
        baseUrl
      );
      setProviders((items) => [
        result,
        ...items.filter((item) => item.provider !== result.provider),
      ]);
      setApiKey("");
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("providerFailed")));
    } finally {
      setSavingProvider(false);
    }
  };

  const saveRuntime = async (event: React.FormEvent) => {
    event.preventDefault();
    setSavingRuntime(true);
    setError(null);
    try {
      const numericValue =
        runtimeKey === "hard_token_cap" ? Number(runtimeValue) : runtimeValue;
      if (!tenantId) {
        throw new Error(t("noTenant"));
      }
      const result = await settingsApi.saveRuntimeSetting(tenantId, runtimeKey, {
        value: numericValue,
      });
      setRuntimeResult(result);
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("runtimeFailed")));
    } finally {
      setSavingRuntime(false);
    }
  };

  const saveEgressPolicy = async (event: React.FormEvent) => {
    event.preventDefault();
    setSavingEgress(true);
    setError(null);
    try {
      if (!tenantId) {
        throw new Error(t("noTenant"));
      }
      const parsed = JSON.parse(egressPolicy) as Record<string, unknown>;
      const result = await settingsApi.saveRuntimeSetting(tenantId, "egress_policy", {
        value: parsed,
      });
      setEgressResult(result);
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("egressFailed")));
    } finally {
      setSavingEgress(false);
    }
  };

  const estimateCost = async (event: React.FormEvent) => {
    event.preventDefault();
    setEstimatingCost(true);
    setError(null);
    try {
      if (!tenantId) {
        throw new Error(t("noTenant"));
      }
      const parsedInput = Number(inputTokens);
      const parsedOutput = Number(outputTokens);
      if (
        !Number.isFinite(parsedInput) ||
        !Number.isFinite(parsedOutput) ||
        parsedInput < 0 ||
        parsedOutput < 0
      ) {
        throw new Error(t("invalidNumber"));
      }
      const result = await settingsApi.estimateCost(tenantId, {
        provider: estimateProvider,
        model_id: estimateModel,
        stage: estimateStage,
        classification: estimateClassification,
        input_tokens: parsedInput,
        output_tokens: parsedOutput,
      });
      setCostEstimate(result);
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("estimateFailed")));
    } finally {
      setEstimatingCost(false);
    }
  };

  const planReindex = async (event: React.FormEvent) => {
    event.preventDefault();
    setPlanningReindex(true);
    setError(null);
    try {
      if (!tenantId) {
        throw new Error(t("noTenant"));
      }
      const parsedDim = Number(reindexDim);
      if (!Number.isFinite(parsedDim) || parsedDim < 1) {
        throw new Error(t("invalidNumber"));
      }
      const result = await settingsApi.planEmbeddingReindex(tenantId, {
        to_model: reindexTargetModel,
        embedding_dim: parsedDim,
      });
      setReindexPlan(result);
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("reindexFailed")));
    } finally {
      setPlanningReindex(false);
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
        <div className="font-mono text-xs text-bone-dim border border-hairline px-3 py-2">
          {t("tenant")}{" "}
          <span className="text-bone">
            {loadingTenants ? t("loading") : tenantId ?? t("none")}
          </span>
        </div>
      </header>

      {error && (
        <Panel classification="restricted" label={t("settingsError")}>
          <p className="px-5 py-4 font-mono text-xs text-seal-red">{error}</p>
        </Panel>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_0.9fr] gap-6">
        <Panel classification="confidential" label={t("providerCredentials")}>
          <form onSubmit={saveProvider} className="px-5 py-5 space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <label className="space-y-1.5">
                <span className="mono-meta block">{t("provider")}</span>
                <select
                  value={provider}
                  onChange={(event) => setProvider(event.target.value as ProviderName)}
                  className="h-10 w-full bg-vault-raised border border-hairline px-3 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
                >
                  {PROVIDERS.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-1.5">
                <span className="mono-meta block">{t("baseUrl")}</span>
                <Input
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  placeholder={t("providerDefault")}
                  className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10"
                />
              </label>
            </div>
            <label className="space-y-1.5 block">
              <span className="mono-meta block">{t("apiKey")}</span>
              <Input
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                type="password"
                autoComplete="off"
                className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10"
              />
            </label>
            <Button type="submit" disabled={savingProvider || !apiKey || !tenantId} className="gap-2">
              {savingProvider ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
              ) : (
                <KeyRound className="h-4 w-4" />
              )}
              {t("verifyAndSave")}
            </Button>
          </form>
        </Panel>

        <Panel classification="internal" label={t("runtimeSetting")}>
          <form onSubmit={saveRuntime} className="px-5 py-5 space-y-4">
            <label className="space-y-1.5 block">
              <span className="mono-meta block">{t("key")}</span>
              <select
                value={runtimeKey}
                onChange={(event) =>
                  setRuntimeKey(event.target.value as (typeof RUNTIME_KEYS)[number])
                }
                className="h-10 w-full bg-vault-raised border border-hairline px-3 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
              >
                {RUNTIME_KEYS.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1.5 block">
              <span className="mono-meta block">{t("value")}</span>
              <Input
                value={runtimeValue}
                onChange={(event) => setRuntimeValue(event.target.value)}
                className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10"
              />
            </label>
            <Button type="submit" disabled={savingRuntime || !tenantId} className="gap-2">
              <Save className="h-4 w-4" />
              {t("saveSetting")}
            </Button>
            {runtimeResult && (
              <div className="border border-hairline bg-vault-sunk px-3 py-2 font-mono text-xs text-bone-dim">
                v{runtimeResult.version} ·{" "}
                {runtimeResult.restart_required ? t("restartRequired") : t("runtimeSafe")}
              </div>
            )}
          </form>
        </Panel>
      </div>

      <Panel label={t("configuredProviders")}>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-px bg-hairline">
          {PROVIDERS.map((item) => {
            const row = configured.get(item);
            return (
              <div key={item} className="bg-vault-raised px-4 py-4 space-y-3">
                <div className="flex items-center justify-between gap-3">
                  <p className="font-display text-sm font-bold text-bone">{item}</p>
                  {row?.last_test_ok ? (
                    <CheckCircle2 className="h-4 w-4 text-signal-amber" />
                  ) : (
                    <ShieldAlert className="h-4 w-4 text-bone-dim" />
                  )}
                </div>
                <dl className="space-y-2 font-mono text-xs">
                  <div className="flex justify-between gap-3">
                    <dt className="text-bone-dim">{t("status")}</dt>
                    <dd className="text-bone">{row?.status ?? t("notConfigured")}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-bone-dim">{t("fingerprint")}</dt>
                    <dd className="text-bone truncate">{row?.fingerprint ?? "—"}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-bone-dim">{t("models")}</dt>
                    <dd className="text-bone tabular-nums">
                      {row?.models_discovered ?? "—"}
                    </dd>
                  </div>
                </dl>
              </div>
            );
          })}
        </div>
      </Panel>

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_0.9fr] gap-6">
        <Panel classification="confidential" label={t("egressPolicy")}>
          <form onSubmit={saveEgressPolicy} className="px-5 py-5 space-y-4">
            <label className="space-y-1.5 block">
              <span className="mono-meta block">{t("policyJson")}</span>
              <textarea
                value={egressPolicy}
                onChange={(event) => setEgressPolicy(event.target.value)}
                className="min-h-56 w-full resize-y bg-vault-raised border border-hairline px-3 py-2 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
                spellCheck={false}
              />
            </label>
            <Button type="submit" disabled={savingEgress || !tenantId} className="gap-2">
              <ShieldAlert className="h-4 w-4" />
              {t("saveEgress")}
            </Button>
            {egressResult && (
              <div className="border border-hairline bg-vault-sunk px-3 py-2 font-mono text-xs text-bone-dim">
                v{egressResult.version} · {t("runtimeSafe")}
              </div>
            )}
          </form>
        </Panel>

        <Panel classification="internal" label={t("costEstimate")}>
          <form onSubmit={estimateCost} className="px-5 py-5 space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <label className="space-y-1.5">
                <span className="mono-meta block">{t("provider")}</span>
                <select
                  value={estimateProvider}
                  onChange={(event) =>
                    setEstimateProvider(event.target.value as ProviderName)
                  }
                  className="h-10 w-full bg-vault-raised border border-hairline px-3 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
                >
                  {PROVIDERS.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-1.5">
                <span className="mono-meta block">{t("model")}</span>
                <Input
                  value={estimateModel}
                  onChange={(event) => setEstimateModel(event.target.value)}
                  className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10"
                />
              </label>
              <label className="space-y-1.5">
                <span className="mono-meta block">{t("stage")}</span>
                <select
                  value={estimateStage}
                  onChange={(event) => setEstimateStage(event.target.value as EgressStage)}
                  className="h-10 w-full bg-vault-raised border border-hairline px-3 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
                >
                  {["generation", "extract", "judge", "embedding"].map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-1.5">
                <span className="mono-meta block">{t("classification")}</span>
                <select
                  value={estimateClassification}
                  onChange={(event) =>
                    setEstimateClassification(event.target.value as EgressClassification)
                  }
                  className="h-10 w-full bg-vault-raised border border-hairline px-3 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber"
                >
                  {["public", "internal", "confidential", "restricted"].map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-1.5">
                <span className="mono-meta block">{t("inputTokens")}</span>
                <Input
                  value={inputTokens}
                  onChange={(event) => setInputTokens(event.target.value)}
                  type="number"
                  min="0"
                  className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10"
                />
              </label>
              <label className="space-y-1.5">
                <span className="mono-meta block">{t("outputTokens")}</span>
                <Input
                  value={outputTokens}
                  onChange={(event) => setOutputTokens(event.target.value)}
                  type="number"
                  min="0"
                  className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10"
                />
              </label>
            </div>
            <Button type="submit" disabled={estimatingCost || !tenantId} className="gap-2">
              <Calculator className="h-4 w-4" />
              {t("estimate")}
            </Button>
            {costEstimate && (
              <dl className="grid grid-cols-2 gap-px bg-hairline font-mono text-xs">
                {[
                  [t("totalTokens"), costEstimate.total_tokens],
                  [t("hardCap"), costEstimate.hard_token_cap],
                  [t("withinCap"), costEstimate.within_hard_token_cap ? t("yes") : t("no")],
                  [t("estimatedUsd"), costEstimate.estimated_cost_usd.toFixed(6)],
                  [t("egress"), costEstimate.egress.action],
                  [t("egressRule"), costEstimate.egress.reason],
                ].map(([label, value]) => (
                  <div key={String(label)} className="bg-vault-sunk px-3 py-2">
                    <dt className="text-bone-dim">{label}</dt>
                    <dd className="text-bone">{value}</dd>
                  </div>
                ))}
              </dl>
            )}
          </form>
        </Panel>
      </div>

      <Panel classification="internal" label={t("blueGreenReindex")}>
        <form onSubmit={planReindex} className="px-5 py-5 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <label className="space-y-1.5">
              <span className="mono-meta block">{t("targetEmbedding")}</span>
              <Input
                value={reindexTargetModel}
                onChange={(event) => setReindexTargetModel(event.target.value)}
                className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10"
              />
            </label>
            <label className="space-y-1.5">
              <span className="mono-meta block">{t("embeddingDim")}</span>
              <Input
                value={reindexDim}
                onChange={(event) => setReindexDim(event.target.value)}
                type="number"
                min="1"
                className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10"
              />
            </label>
          </div>
          <Button type="submit" disabled={planningReindex || !tenantId} className="gap-2">
            <RefreshCw className="h-4 w-4" />
            {t("planReindex")}
          </Button>
          {reindexPlan && (
            <dl className="grid grid-cols-1 md:grid-cols-3 gap-px bg-hairline font-mono text-xs">
              {[
                [t("status"), reindexPlan.status],
                [t("activeCollection"), reindexPlan.active_collection],
                [t("shadowCollection"), reindexPlan.shadow_collection],
              ].map(([label, value]) => (
                <div key={String(label)} className="bg-vault-sunk px-3 py-2">
                  <dt className="text-bone-dim">{label}</dt>
                  <dd className="text-bone break-all">{value}</dd>
                </div>
              ))}
            </dl>
          )}
        </form>
      </Panel>
    </div>
  );
}
