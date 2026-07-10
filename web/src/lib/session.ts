"use client";

import { useEffect, useState } from "react";
import type { SessionUser } from "./types";

export function clearSession(): void {
  if (typeof window === "undefined") {
    return;
  }
  localStorage.removeItem("securgraph_user");
  localStorage.removeItem("securgraph_tenant");
}

export function getSessionUser(): SessionUser | null {
  if (typeof window === "undefined") {
    return null;
  }
  const raw = localStorage.getItem("securgraph_user");
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw) as SessionUser;
  } catch {
    return null;
  }
}

/** Session user for display purposes (name, initials). Authorization is
 * always enforced server-side from the cookie — this is never authoritative. */
export function useSessionUser(): SessionUser | null {
  const [user, setUser] = useState<SessionUser | null>(null);
  useEffect(() => {
    setUser(getSessionUser());
  }, []);
  return user;
}

export function displayName(user: SessionUser | null): string {
  return user?.display_name || user?.username || "Operator";
}

export function initials(user: SessionUser | null): string {
  const name = displayName(user);
  const parts = name.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}
