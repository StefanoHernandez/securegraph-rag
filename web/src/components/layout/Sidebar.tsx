"use client";

import React, { useEffect, useRef } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import { localizePath, stripLocale } from "@/lib/i18n";
import { useTenant as useTenantSelection } from "@/context/TenantProvider";
import { displayName, initials, useSessionUser } from "@/lib/session";
import {
  LayoutDashboard,
  MessageSquare,
  UploadCloud,
  GitBranch,
  ShieldAlert,
  Activity,
  ScrollText,
  Network,
  ScatterChart,
  Settings,
  Cpu,
  Users,
  Building2,
  UsersRound,
  ClipboardList,
  Wrench,
  ChevronLeft,
  ChevronRight,
  X,
} from "lucide-react";

interface SidebarProps {
  collapsed: boolean;
  setCollapsed: (collapsed: boolean) => void;
  mobileOpen: boolean;
  onMobileClose: () => void;
}

// Dossier index — order follows the evidence lifecycle, not alphabetised nav.
const navItems = [
  { labelKey: "briefing", code: "00", href: "/", icon: LayoutDashboard },
  { labelKey: "query", code: "01", href: "/chat", icon: MessageSquare },
  { labelKey: "intake", code: "02", href: "/ingest", icon: UploadCloud },
  { labelKey: "provenance", code: "03", href: "/explain", icon: GitBranch },
  { labelKey: "clearance", code: "04", href: "/permissions", icon: ShieldAlert },
  { labelKey: "graph", code: "05", href: "/graph", icon: Network },
  { labelKey: "vectors", code: "05b", href: "/vectors", icon: ScatterChart },
  { labelKey: "custodyLedger", code: "06", href: "/audit", icon: ScrollText },
  { labelKey: "health", code: "07", href: "/health", icon: Activity },
  { labelKey: "settings", code: "08", href: "/settings", icon: Settings },
  { labelKey: "models", code: "09", href: "/models", icon: Cpu },
  { labelKey: "users", code: "10", href: "/users", icon: Users },
  { labelKey: "tenants", code: "11", href: "/tenants", icon: Building2 },
  { labelKey: "groups", code: "12", href: "/groups", icon: UsersRound },
  { labelKey: "accessTemplates", code: "13", href: "/access-templates", icon: ClipboardList },
  { labelKey: "operations", code: "14", href: "/operations", icon: Wrench },
];

export default function Sidebar({
  collapsed,
  setCollapsed,
  mobileOpen,
  onMobileClose,
}: SidebarProps) {
  const nav = useTranslations("navigation");
  const shell = useTranslations("shell");
  const pathname = usePathname();
  const activePath = stripLocale(pathname);
  const { tenantId } = useTenantSelection();
  const sessionUser = useSessionUser();
  const homeHref = localizePath("/", pathname);
  const sidebarRef = useRef<HTMLElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const displayCollapsed = collapsed && !mobileOpen;

  useEffect(() => {
    if (!mobileOpen) {
      return;
    }
    restoreFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const focusable = () =>
      Array.from(
        sidebarRef.current?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        ) ?? []
      );
    const focusFirst = () => focusable()[0]?.focus();
    const frame = window.requestAnimationFrame(focusFirst);
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onMobileClose();
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      const items = focusable();
      if (items.length === 0) {
        event.preventDefault();
        sidebarRef.current?.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", handleKeyDown);
      restoreFocusRef.current?.focus();
      restoreFocusRef.current = null;
    };
  }, [mobileOpen, onMobileClose]);

  return (
    <>
      {mobileOpen && (
        <button
          type="button"
          aria-label={shell("closeNavigation")}
          className="fixed inset-0 z-30 bg-black/60 md:hidden"
          onClick={onMobileClose}
        />
      )}
      <aside
        ref={sidebarRef}
        id="primary-navigation"
        tabIndex={-1}
        role={mobileOpen ? "dialog" : undefined}
        aria-modal={mobileOpen || undefined}
        aria-label={shell("navigation")}
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex h-screen w-60 -translate-x-full flex-col border-r border-hairline bg-sidebar transition-transform duration-300 md:sticky md:top-0 md:z-30 md:translate-x-0 md:transition-[width]",
          mobileOpen && "translate-x-0",
          displayCollapsed ? "md:w-16" : "md:w-60"
        )}
      >
      {/* Brand */}
      <div className="h-16 flex items-center justify-between px-4 border-b border-hairline">
        {!displayCollapsed ? (
          <Link href={homeHref} onClick={onMobileClose} className="flex flex-col leading-none select-none">
            <span className="font-display text-base font-bold tracking-[0.18em] text-bone">
              SECURE<span className="text-signal-amber">GRAPH</span>
            </span>
            <span className="mono-meta mt-1">verifiable rag · vault</span>
          </Link>
        ) : (
          <Link
            href={homeHref}
            onClick={onMobileClose}
            className="mx-auto font-display text-base font-bold text-signal-amber select-none"
          >
            S
          </Link>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? shell("expandSidebar") : shell("collapseSidebar")}
          className="hidden md:flex p-1 text-bone-dim hover:text-bone transition"
        >
          {collapsed ? (
            <ChevronRight className="h-4 w-4" />
          ) : (
            <ChevronLeft className="h-4 w-4" />
          )}
        </button>
        <button
          type="button"
          onClick={onMobileClose}
          aria-label={shell("closeNavigation")}
          className="flex p-1 text-bone-dim transition hover:text-bone md:hidden"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Nav */}
      <nav className="flex-1 py-3 overflow-y-auto custom-scrollbar">
        {navItems.map((item) => {
          const href = localizePath(item.href, pathname);
          const isActive = activePath === item.href;
          const label = nav(item.labelKey);
          return (
            <Link
              key={item.labelKey}
              href={href}
              onClick={onMobileClose}
              className={cn(
                "group relative flex items-center gap-3 px-4 py-2.5 text-sm transition-colors",
                isActive
                  ? "text-bone"
                  : "text-bone-dim hover:text-bone hover:bg-[rgba(232,230,223,0.03)]"
              )}
            >
              {/* active marker — amber, structural not decorative */}
              <span
                className={cn(
                  "absolute left-0 top-0 bottom-0 w-[2px] transition-colors",
                  isActive ? "bg-signal-amber" : "bg-transparent"
                )}
              />
              <item.icon className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
              {!displayCollapsed && (
                <>
                  <span className="flex-1">{label}</span>
                  <span className="font-mono text-[0.625rem] text-bone-dim/70">
                    {item.code}
                  </span>
                </>
              )}
            </Link>
          );
        })}
      </nav>

      {/* Footer — session clearance */}
      <div className="p-4 border-t border-hairline">
        {!displayCollapsed ? (
          <div className="space-y-1.5">
            <p className="mono-meta">{shell("operator")}</p>
            <p className="text-sm text-bone truncate">{displayName(sessionUser)}</p>
            <p className="mono-meta">
              {shell("tenant")} · {tenantId ?? shell("none")}
            </p>
          </div>
        ) : (
          <div className="mx-auto h-7 w-7 border border-hairline flex items-center justify-center font-mono text-xs text-bone">
            {initials(sessionUser)}
          </div>
        )}
      </div>
      </aside>
    </>
  );
}
