"use client";

import React from "react";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import { localizePath, stripLocale } from "@/lib/i18n";
import { Menu, LogOut, Settings, User, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ClassificationStamp } from "@/components/forensic";
import { authApi } from "@/lib/api";
import { useTenantSelection } from "@/lib/useTenantSelection";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface TopBarProps {
  setSidebarCollapsed: () => void;
}

const TITLES: Record<string, { eyebrow: string; titleKey: string }> = {
  "/": { eyebrow: "section 00", titleKey: "briefing" },
  "/chat": { eyebrow: "section 01", titleKey: "query" },
  "/ingest": { eyebrow: "section 02", titleKey: "intake" },
  "/explain": { eyebrow: "section 03", titleKey: "provenance" },
  "/permissions": { eyebrow: "section 04", titleKey: "clearance" },
  "/graph": { eyebrow: "section 05", titleKey: "graph" },
  "/audit": { eyebrow: "section 06", titleKey: "custodyLedger" },
  "/health": { eyebrow: "section 07", titleKey: "health" },
  "/settings": { eyebrow: "section 08", titleKey: "settings" },
  "/models": { eyebrow: "section 09", titleKey: "models" },
  "/users": { eyebrow: "section 10", titleKey: "users" },
  "/tenants": { eyebrow: "section 11", titleKey: "tenants" },
  "/groups": { eyebrow: "section 12", titleKey: "groups" },
  "/access-templates": { eyebrow: "section 13", titleKey: "accessTemplates" },
  "/operations": { eyebrow: "section 14", titleKey: "operations" },
  "/profile": { eyebrow: "operator", titleKey: "profile" },
};

export default function TopBar({ setSidebarCollapsed }: TopBarProps) {
  const topbar = useTranslations("topbar");
  const shell = useTranslations("shell");
  const pathname = usePathname();
  const activePath = stripLocale(pathname);
  const router = useRouter();
  const { tenants, tenantId, loadingTenants, selectTenant } = useTenantSelection();

  const meta =
    TITLES[activePath] ??
    (activePath.startsWith("/explain")
      ? { eyebrow: "section 03", titleKey: "provenanceDetail" }
      : { eyebrow: "secure", titleKey: "fallback" });

  const handleTenantChange = (selected: string) => {
    selectTenant(selected);
    window.location.reload();
  };

  const handleLogout = async () => {
    await authApi.logout().catch(() => undefined);
    localStorage.removeItem("securgraph_user");
    router.push(localizePath("/login", pathname));
  };

  return (
    <header className="h-16 border-b border-hairline bg-vault/80 backdrop-blur-sm sticky top-0 z-20 flex items-center justify-between px-5 md:px-7">
      <div className="flex items-center gap-4">
        <Button
          variant="ghost"
          size="icon"
          onClick={setSidebarCollapsed}
          className="md:hidden text-bone-dim"
          aria-label={shell("toggleNavigation")}
        >
          <Menu className="h-5 w-5" />
        </Button>
        <div className="flex flex-col leading-tight">
          <span className="mono-meta">{meta.eyebrow}</span>
          <span className="font-display text-sm font-semibold tracking-wide text-bone">
            {topbar(meta.titleKey)}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-3">
        {/* Session clearance stamp */}
        <ClassificationStamp level="restricted" className="hidden sm:inline-flex" />

        {/* Tenant selector */}
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                variant="outline"
                size="sm"
                className="gap-2 h-8 font-mono text-[0.6875rem] uppercase tracking-wider"
                disabled={loadingTenants || tenants.length === 0}
              >
                <span className="text-bone-dim">{shell("tenant")}</span>
                <span className="text-bone">{tenantId ?? shell("none")}</span>
                <ChevronDown className="h-3 w-3 text-bone-dim" />
              </Button>
            }
          />
          <DropdownMenuContent className="w-52">
            <DropdownMenuGroup>
              <DropdownMenuLabel className="mono-meta">
                {shell("switchTenant")}
              </DropdownMenuLabel>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            {tenants.map((tenant) => (
              <DropdownMenuItem
                key={tenant.id}
                onClick={() => handleTenantChange(tenant.id)}
                className={cn(
                  "font-mono text-xs cursor-pointer",
                  tenantId === tenant.id && "text-signal-amber"
                )}
              >
                {tenant.display_name}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Operator menu */}
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                variant="outline"
                size="icon"
                className="h-8 w-8"
                aria-label={shell("operatorMenu")}
              >
                <User className="h-4 w-4" />
              </Button>
            }
          />
          <DropdownMenuContent className="w-56" align="end">
            <DropdownMenuGroup>
              <DropdownMenuLabel className="font-normal">
                <div className="flex flex-col gap-1">
                  <p className="text-sm text-bone">Stefano Hernandez</p>
                  <p className="mono-meta">
                    {shell("operator")} · {shell("clearanceRestricted")}
                  </p>
                </div>
              </DropdownMenuLabel>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-xs cursor-pointer gap-2"
              onClick={() => router.push(localizePath("/profile", pathname))}
            >
              <User className="h-3.5 w-3.5 text-bone-dim" />
              {shell("profile")}
            </DropdownMenuItem>
            <DropdownMenuItem
              className="text-xs cursor-pointer gap-2"
              onClick={() => router.push(localizePath("/settings", pathname))}
            >
              <Settings className="h-3.5 w-3.5 text-bone-dim" />
              {shell("settings")}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-xs cursor-pointer text-seal-red gap-2"
              onClick={handleLogout}
            >
              <LogOut className="h-3.5 w-3.5" />
              {shell("logout")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
