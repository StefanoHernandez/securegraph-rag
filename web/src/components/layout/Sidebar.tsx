"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  MessageSquare,
  UploadCloud,
  HelpCircle,
  ShieldAlert,
  HeartPulse,
  FileCode,
  Network,
  ChevronLeft,
  ChevronRight,
  ShieldCheck,
  UserCheck
} from "lucide-react";

interface SidebarProps {
  collapsed: boolean;
  setCollapsed: (collapsed: boolean) => void;
}

export default function Sidebar({ collapsed, setCollapsed }: SidebarProps) {
  const pathname = usePathname();

  const navItems = [
    { name: "Dashboard", href: "/", icon: LayoutDashboard },
    { name: "Chat / Query", href: "/chat", icon: MessageSquare },
    { name: "Ingestion", href: "/ingest", icon: UploadCloud },
    { name: "Provenance", href: "/explain", icon: HelpCircle },
    { name: "Permissions", href: "/permissions", icon: ShieldAlert },
    { name: "System Health", href: "/health", icon: HeartPulse },
    { name: "Audit Ledger", href: "/audit", icon: FileCode },
    { name: "Graph Explorer", href: "/graph", icon: Network },
  ];

  return (
    <aside
      className={cn(
        "glassmorphism h-screen sticky top-0 flex flex-col transition-all duration-300 z-30 border-r border-border/50",
        collapsed ? "w-16" : "w-64"
      )}
    >
      {/* Brand Header */}
      <div className="h-16 flex items-center justify-between px-4 border-b border-border/30">
        {!collapsed && (
          <Link href="/" className="flex items-center gap-2 font-bold text-lg select-none">
            <span className="p-1 rounded bg-accent-indigo text-black">
              <ShieldCheck className="h-5 w-5" />
            </span>
            <span className="gradient-text font-black tracking-tight">SecurGraph</span>
          </Link>
        )}
        {collapsed && (
          <Link href="/" className="mx-auto flex items-center justify-center p-1 rounded bg-accent-indigo text-black">
            <ShieldCheck className="h-5 w-5" />
          </Link>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="hidden md:flex p-1 rounded-md text-foreground/75 hover:bg-white/5 hover:text-foreground transition"
        >
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </button>
      </div>

      {/* Nav Menu Items */}
      <nav className="flex-1 py-4 px-2 space-y-1 overflow-y-auto custom-scrollbar">
        {navItems.map((item) => {
          const isActive = pathname === item.href;
          return (
            <Link
              key={item.name}
              href={item.href}
              className={cn(
                "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition group",
                isActive
                  ? "bg-accent-indigo/15 text-accent-indigo border-l-2 border-accent-indigo"
                  : "text-foreground/70 hover:bg-white/5 hover:text-foreground"
              )}
            >
              <item.icon
                className={cn(
                  "h-5 w-5 flex-shrink-0 transition",
                  isActive ? "text-accent-indigo" : "text-foreground/50 group-hover:text-foreground"
                )}
              />
              {!collapsed && <span>{item.name}</span>}
            </Link>
          );
        })}
      </nav>

      {/* Footer Info */}
      <div className="p-4 border-t border-border/30 bg-black/20 flex flex-col gap-2">
        <div className="flex items-center gap-3">
          <div className="h-8 w-8 rounded-full bg-accent-indigo/20 flex items-center justify-center text-accent-indigo font-bold border border-accent-indigo/30">
            <UserCheck className="h-4 w-4" />
          </div>
          {!collapsed && (
            <div className="flex-1 min-w-0">
              <p className="text-xs font-semibold truncate text-foreground">Stefano H.</p>
              <p className="text-[10px] text-foreground/50 truncate uppercase tracking-wider">Tenant: default</p>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}
