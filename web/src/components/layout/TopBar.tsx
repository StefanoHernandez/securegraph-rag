"use client";

import React, { useState, useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { Menu, LogOut, Settings, User, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ClassificationStamp } from "@/components/forensic";
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

const TENANTS = ["default", "mrd", "demo_tenant", "test_tenant_async"];

const TITLES: Record<string, { eyebrow: string; title: string }> = {
  "/": { eyebrow: "section 00", title: "Operator Briefing" },
  "/chat": { eyebrow: "section 01", title: "Query Console" },
  "/ingest": { eyebrow: "section 02", title: "Evidence Intake" },
  "/explain": { eyebrow: "section 03", title: "Provenance" },
  "/permissions": { eyebrow: "section 04", title: "Clearance Control" },
  "/graph": { eyebrow: "section 05", title: "Knowledge Graph" },
  "/audit": { eyebrow: "section 06", title: "Chain of Custody" },
  "/health": { eyebrow: "section 07", title: "System Health" },
};

export default function TopBar({ setSidebarCollapsed }: TopBarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [tenant, setTenant] = useState("default");

  const meta =
    TITLES[pathname ?? "/"] ??
    (pathname?.startsWith("/explain")
      ? { eyebrow: "section 03", title: "Provenance Detail" }
      : { eyebrow: "secure", title: "SecureGraph" });

  useEffect(() => {
    const saved = localStorage.getItem("securgraph_tenant");
    if (saved) setTenant(saved);
  }, []);

  const handleTenantChange = (selected: string) => {
    setTenant(selected);
    localStorage.setItem("securgraph_tenant", selected);
    window.location.reload();
  };

  const handleLogout = () => {
    localStorage.removeItem("securgraph_token");
    localStorage.removeItem("securgraph_user");
    router.push("/login");
  };

  return (
    <header className="h-16 border-b border-hairline bg-vault/80 backdrop-blur-sm sticky top-0 z-20 flex items-center justify-between px-5 md:px-7">
      <div className="flex items-center gap-4">
        <Button
          variant="ghost"
          size="icon"
          onClick={setSidebarCollapsed}
          className="md:hidden text-bone-dim"
          aria-label="Toggle navigation"
        >
          <Menu className="h-5 w-5" />
        </Button>
        <div className="flex flex-col leading-tight">
          <span className="mono-meta">{meta.eyebrow}</span>
          <span className="font-display text-sm font-semibold tracking-wide text-bone">
            {meta.title}
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
              >
                <span className="text-bone-dim">tenant</span>
                <span className="text-bone">{tenant}</span>
                <ChevronDown className="h-3 w-3 text-bone-dim" />
              </Button>
            }
          />
          <DropdownMenuContent className="w-52">
            <DropdownMenuGroup>
              <DropdownMenuLabel className="mono-meta">
                switch tenant
              </DropdownMenuLabel>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            {TENANTS.map((t) => (
              <DropdownMenuItem
                key={t}
                onClick={() => handleTenantChange(t)}
                className={cn(
                  "font-mono text-xs cursor-pointer",
                  tenant === t && "text-signal-amber"
                )}
              >
                {t}
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
                aria-label="Operator menu"
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
                  <p className="mono-meta">operator · clearance restricted</p>
                </div>
              </DropdownMenuLabel>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-xs cursor-pointer gap-2"
              onClick={() => router.push("/")}
            >
              <User className="h-3.5 w-3.5 text-bone-dim" />
              Profile
            </DropdownMenuItem>
            <DropdownMenuItem className="text-xs cursor-pointer gap-2">
              <Settings className="h-3.5 w-3.5 text-bone-dim" />
              Settings
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-xs cursor-pointer text-seal-red gap-2"
              onClick={handleLogout}
            >
              <LogOut className="h-3.5 w-3.5" />
              Log out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
