"use client";

import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/layout/Sidebar";
import TopBar from "@/components/layout/TopBar";
import { authApi } from "@/lib/api";

export default function DashboardShell({
  children,
}: {
  children: React.ReactNode;
}) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [authChecked, setAuthChecked] = useState(false);
  const router = useRouter();

  useEffect(() => {
    let active = true;
    authApi
      .me()
      .then((user) => {
        if (!active) {
          return;
        }
        localStorage.setItem("securgraph_user", JSON.stringify(user));
        if (user.tenant_id) {
          localStorage.setItem("securgraph_tenant", user.tenant_id);
        }
        setAuthChecked(true);
      })
      .catch(() => {
        if (active) {
          // Preserve the page the user was on so login can return there.
          const path = window.location.pathname + window.location.search;
          const next = path && path !== "/login" ? `?next=${encodeURIComponent(path)}` : "";
          router.push(`/login${next}`);
        }
      });
    return () => {
      active = false;
    };
  }, [router]);

  if (!authChecked) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-vault text-foreground">
        <div className="flex flex-col items-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-signal-amber border-t-transparent"></div>
          <p className="mono-meta">verifying clearance…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen w-full relative">
      <Sidebar collapsed={sidebarCollapsed} setCollapsed={setSidebarCollapsed} />
      <div className="flex flex-col flex-1 min-w-0">
        <TopBar setSidebarCollapsed={() => setSidebarCollapsed(!sidebarCollapsed)} />
        <main className="flex-1 p-6 md:p-8 overflow-y-auto custom-scrollbar">
          {children}
        </main>
      </div>
    </div>
  );
}
