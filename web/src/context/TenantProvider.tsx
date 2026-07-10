"use client";

import React, { createContext, useContext } from "react";
import { useTenantSelection } from "@/lib/useTenantSelection";
import type { TenantSummary } from "@/lib/types";

type TenantContextValue = ReturnType<typeof useTenantSelection>;

const TenantContext = createContext<TenantContextValue | null>(null);

export function TenantProvider({ children }: { children: React.ReactNode }) {
  const value = useTenantSelection();
  return <TenantContext.Provider value={value}>{children}</TenantContext.Provider>;
}

export function useTenant(): TenantContextValue {
  const context = useContext(TenantContext);
  if (context === null) {
    throw new Error("useTenant must be used within TenantProvider");
  }
  return context;
}

export type { TenantSummary };
