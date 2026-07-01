"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { CheckCircle2, KeyRound, Save, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel } from "@/components/forensic";
import { getErrorMessage } from "@/lib/errors";
import { settingsApi } from "@/lib/api";
import {
  ProviderCredentialStatus,
  ProviderName,
  RuntimeSetting,
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
    </div>
  );
}
