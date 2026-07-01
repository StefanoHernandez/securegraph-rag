"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Building2, Plus, RefreshCw, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel } from "@/components/forensic";
import { controlPlaneApi } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { TenantDetail, TenantStatus } from "@/lib/types";

const STATUSES: TenantStatus[] = ["active", "suspended", "archived"];

export default function TenantsPage() {
  const t = useTranslations("tenantsPage");
  const [tenants, setTenants] = useState<TenantDetail[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadTenants = useCallback(async () => {
    setLoading(true);
    try {
      setTenants(await controlPlaneApi.listTenants());
      setError(null);
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("loadError")));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadTenants(), 0);
    return () => window.clearTimeout(timer);
  }, [loadTenants]);

  const createTenant = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const created = await controlPlaneApi.createTenant(tenantId, displayName);
      setTenants((items) => [...items, created].sort((a, b) => a.display_name.localeCompare(b.display_name)));
      setTenantId("");
      setDisplayName("");
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("createError")));
    } finally {
      setSaving(false);
    }
  };

  const updateTenant = async (
    tenant: TenantDetail,
    body: {
      display_name?: string;
      status?: TenantStatus;
      tenant_admin_settings_enabled?: boolean;
    }
  ) => {
    setError(null);
    try {
      const updated = await controlPlaneApi.updateTenant(tenant.id, body);
      setTenants((items) => items.map((item) => (item.id === updated.id ? updated : item)));
    } catch (err: unknown) {
      setError(getErrorMessage(err, t("updateError")));
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
        <Button size="sm" onClick={loadTenants} disabled={loading} className="gap-2">
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          {t("refresh")}
        </Button>
      </header>

      {error && (
        <Panel classification="restricted" label={t("error")}>
          <p className="px-5 py-4 font-mono text-xs text-seal-red">{error}</p>
        </Panel>
      )}

      <Panel classification="confidential" label={t("createTenant")}>
        <form onSubmit={createTenant} className="px-5 py-5 grid grid-cols-1 md:grid-cols-[1fr_1.4fr_auto] gap-4">
          <label className="space-y-1.5">
            <span className="mono-meta block">{t("technicalId")}</span>
            <Input value={tenantId} onChange={(event) => setTenantId(event.target.value)} className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10" />
          </label>
          <label className="space-y-1.5">
            <span className="mono-meta block">{t("displayName")}</span>
            <Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} className="bg-vault-raised border-hairline text-bone font-mono text-xs h-10" />
          </label>
          <div className="flex items-end">
            <Button type="submit" disabled={saving || !tenantId || !displayName} className="gap-2">
              <Plus className="h-4 w-4" />
              {t("create")}
            </Button>
          </div>
        </form>
      </Panel>

      <Panel label={t("tenantRegistry")}>
        <div className="overflow-x-auto custom-scrollbar">
          <table className="w-full text-left">
            <thead className="border-b border-hairline">
              <tr className="mono-meta">
                <th className="px-4 py-2 font-normal">{t("tenant")}</th>
                <th className="px-4 py-2 font-normal">{t("status")}</th>
                <th className="px-4 py-2 font-normal">{t("settingsDelegation")}</th>
                <th className="px-4 py-2 font-normal">{t("rename")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {tenants.map((tenant) => (
                <TenantRow
                  key={`${tenant.id}:${tenant.display_name}`}
                  tenant={tenant}
                  onUpdate={updateTenant}
                />
              ))}
              {!loading && tenants.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-8 text-sm text-bone-dim">
                    {t("empty")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function TenantRow({
  tenant,
  onUpdate,
}: {
  tenant: TenantDetail;
  onUpdate: (
    tenant: TenantDetail,
    body: {
      display_name?: string;
      status?: TenantStatus;
      tenant_admin_settings_enabled?: boolean;
    }
  ) => void;
}) {
  const t = useTranslations("tenantsPage");
  const [name, setName] = useState(tenant.display_name);

  return (
    <tr>
      <td className="px-4 py-3">
        <div className="flex items-center gap-3">
          <Building2 className="h-4 w-4 text-bone-dim" />
          <div>
            <p className="text-sm text-bone">{tenant.display_name}</p>
            <p className="font-mono text-xs text-bone-dim">{tenant.id}</p>
          </div>
        </div>
      </td>
      <td className="px-4 py-3">
        <select value={tenant.status} onChange={(event) => onUpdate(tenant, { status: event.target.value as TenantStatus })} className="h-8 bg-vault-raised border border-hairline px-2 font-mono text-xs text-bone focus:outline-none focus:border-signal-amber">
          {STATUSES.map((status) => (
            <option key={status} value={status}>{status}</option>
          ))}
        </select>
      </td>
      <td className="px-4 py-3">
        <label className="inline-flex items-center gap-2 font-mono text-xs text-bone-dim">
          <input type="checkbox" checked={tenant.tenant_admin_settings_enabled} onChange={(event) => onUpdate(tenant, { tenant_admin_settings_enabled: event.target.checked })} />
          {t("tenantAdminSettings")}
        </label>
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2 min-w-64">
          <Input value={name} onChange={(event) => setName(event.target.value)} className="bg-vault-raised border-hairline text-bone font-mono text-xs h-8" />
          <Button size="icon-sm" variant="outline" onClick={() => onUpdate(tenant, { display_name: name })} title={t("saveName")}>
            <Save className="h-3.5 w-3.5" />
          </Button>
        </div>
      </td>
    </tr>
  );
}
