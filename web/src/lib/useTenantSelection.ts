"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { tenantApi } from "@/lib/api";
import { TenantSummary } from "@/lib/types";
import { useErrorMessage } from "@/lib/errors";

function readStoredTenant(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  return localStorage.getItem("securgraph_tenant");
}

function readStoredUserTenant(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  const raw = localStorage.getItem("securgraph_user");
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as { tenant_id?: string | null };
    return parsed.tenant_id ?? null;
  } catch {
    return null;
  }
}

export function useTenantSelection() {
  const t = useTranslations("errors");
  const showError = useErrorMessage();
  const [tenants, setTenants] = useState<TenantSummary[]>([]);
  const [tenantId, setTenantId] = useState<string | null>(readStoredTenant);
  const [loadingTenants, setLoadingTenants] = useState(true);
  const [tenantError, setTenantError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    tenantApi
      .listTenants()
      .then((items) => {
        if (!active) {
          return;
        }
        setTenants(items);
        const ids = new Set(items.map((item) => item.id));
        const preferred = readStoredTenant();
        const sessionTenant = readStoredUserTenant();
        const next =
          (preferred && ids.has(preferred) && preferred) ||
          (sessionTenant && ids.has(sessionTenant) && sessionTenant) ||
          items[0]?.id ||
          null;
        setTenantId(next);
        if (next) {
          localStorage.setItem("securgraph_tenant", next);
        }
        setTenantError(null);
      })
      .catch((error: unknown) => {
        if (!active) {
          return;
        }
        setTenantError(showError(error, t("tenantsLoadFailed")));
      })
      .finally(() => {
        if (active) {
          setLoadingTenants(false);
        }
      });
    return () => {
      active = false;
    };
  }, [showError, t]);

  const currentTenant = useMemo(
    () => tenants.find((tenant) => tenant.id === tenantId) ?? null,
    [tenantId, tenants]
  );

  const selectTenant = useCallback((selected: string) => {
    setTenantId(selected);
    localStorage.setItem("securgraph_tenant", selected);
  }, []);

  return {
    tenants,
    tenantId,
    currentTenant,
    loadingTenants,
    tenantError,
    selectTenant,
  };
}
