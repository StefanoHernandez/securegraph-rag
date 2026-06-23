"use client";

import React, { useState, useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { Menu, LogOut, Settings, User, Building } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface TopBarProps {
  setSidebarCollapsed: () => void;
}

export default function TopBar({ setSidebarCollapsed }: TopBarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [tenant, setTenant] = useState("default");

  // Get current page title for breadcrumbs
  const getPageTitle = () => {
    switch (pathname) {
      case "/":
        return "Dashboard";
      case "/chat":
        return "Chat / RAG Query";
      case "/ingest":
        return "Document Ingestion";
      case "/explain":
        return "Provenance Explorer";
      case "/permissions":
        return "Permissions Management";
      case "/health":
        return "System Health";
      case "/audit":
        return "Tamper-Evident Audit Ledger";
      case "/graph":
        return "Knowledge Graph Explorer";
      default:
        if (pathname?.startsWith("/explain/")) return "Provenance Detail";
        return "SecurGraph RAG";
    }
  };

  useEffect(() => {
    const savedTenant = localStorage.getItem("securgraph_tenant");
    if (savedTenant) {
      setTenant(savedTenant);
    }
  }, []);

  const handleTenantChange = (selected: string) => {
    setTenant(selected);
    localStorage.setItem("securgraph_tenant", selected);
    // Reload page or trigger context update to reload data for tenant
    window.location.reload();
  };

  const handleLogout = () => {
    localStorage.removeItem("securgraph_token");
    localStorage.removeItem("securgraph_user");
    router.push("/login");
  };

  return (
    <header className="h-16 border-b border-border/30 bg-black/40 backdrop-blur-md sticky top-0 z-20 flex items-center justify-between px-6">
      {/* Left side: Hamburger + Page Title */}
      <div className="flex items-center gap-4">
        <Button
          variant="ghost"
          size="icon"
          onClick={setSidebarCollapsed}
          className="md:hidden text-foreground/80 hover:bg-white/5"
        >
          <Menu className="h-5 w-5" />
        </Button>
        <div className="flex flex-col">
          <span className="text-[10px] text-foreground/45 uppercase tracking-widest font-mono">SecurGraph RAG</span>
          <span className="text-sm font-semibold text-foreground tracking-tight">{getPageTitle()}</span>
        </div>
      </div>

      {/* Right side: Tenant selector + User Menu */}
      <div className="flex items-center gap-4">
        {/* Tenant Selector */}
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                variant="outline"
                size="sm"
                className="bg-black/30 border-border/50 text-foreground hover:bg-white/5 gap-2 px-3 h-9 text-xs"
              >
                <Building className="h-3.5 w-3.5 text-accent-cyan" />
                <span className="hidden sm:inline">Tenant:</span>
                <span className="font-semibold text-accent-cyan">{tenant}</span>
              </Button>
            }
          />
          <DropdownMenuContent className="w-48 bg-[#12121a] border-border/50 text-foreground">
            <DropdownMenuLabel className="text-xs text-foreground/50">Switch Tenant</DropdownMenuLabel>
            <DropdownMenuSeparator className="bg-border/30" />
            <DropdownMenuItem
              onClick={() => handleTenantChange("default")}
              className={cn("text-xs cursor-pointer", tenant === "default" && "text-accent-cyan font-bold")}
            >
              default
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => handleTenantChange("mrd")}
              className={cn("text-xs cursor-pointer", tenant === "mrd" && "text-accent-cyan font-bold")}
            >
              mrd
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => handleTenantChange("demo_tenant")}
              className={cn("text-xs cursor-pointer", tenant === "demo_tenant" && "text-accent-cyan font-bold")}
            >
              demo_tenant
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => handleTenantChange("test_tenant_async")}
              className={cn("text-xs cursor-pointer", tenant === "test_tenant_async" && "text-accent-cyan font-bold")}
            >
              test_tenant_async
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        {/* User Account Menu */}
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                variant="ghost"
                size="icon"
                className="rounded-full h-9 w-9 bg-accent-indigo/10 border border-accent-indigo/20 text-accent-indigo hover:bg-accent-indigo/25"
              >
                <User className="h-4 w-4" />
              </Button>
            }
          />
          <DropdownMenuContent className="w-56 bg-[#12121a] border-border/50 text-foreground" align="end">
            <DropdownMenuLabel className="font-normal">
              <div className="flex flex-col space-y-1">
                <p className="text-sm font-medium leading-none text-foreground">Stefano Hernandez</p>
                <p className="text-xs leading-none text-foreground/50">stefano@securegraph.local</p>
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator className="bg-border/30" />
            <DropdownMenuItem className="text-xs cursor-pointer gap-2" onClick={() => router.push("/")}>
              <User className="h-3.5 w-3.5 text-foreground/50" />
              Profile
            </DropdownMenuItem>
            <DropdownMenuItem className="text-xs cursor-pointer gap-2">
              <Settings className="h-3.5 w-3.5 text-foreground/50" />
              Settings
            </DropdownMenuItem>
            <DropdownMenuSeparator className="bg-border/30" />
            <DropdownMenuItem
              className="text-xs cursor-pointer text-accent-rose focus:text-accent-rose gap-2"
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
