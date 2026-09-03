"use client";

import { useSyncExternalStore } from "react";
import type { SessionUser } from "./types";

export function clearSession(): void {
  if (typeof window === "undefined") {
    return;
  }
  for (const storage of [localStorage, sessionStorage]) {
    const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index)).filter(
      (key): key is string => key !== null && key.startsWith("securgraph_")
    );
    for (const key of keys) {
      storage.removeItem(key);
    }
  }
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

// useSyncExternalStore compares snapshots by identity, and getSessionUser()
// parses fresh JSON on every call, so returning it directly would re-render
// forever. The parsed value is cached against the raw string it came from and
// only rebuilt when that string actually changes.
let cachedRaw: string | null = null;
let cachedUser: SessionUser | null = null;

function sessionUserSnapshot(): SessionUser | null {
  if (typeof window === "undefined") {
    return null;
  }
  const raw = localStorage.getItem("securgraph_user");
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    cachedUser = getSessionUser();
  }
  return cachedUser;
}

// Rendered on the server before localStorage exists; a distinct server
// snapshot is what keeps hydration consistent.
function serverSessionUserSnapshot(): SessionUser | null {
  return null;
}

function subscribeToSessionUser(onChange: () => void): () => void {
  if (typeof window === "undefined") {
    return () => undefined;
  }
  // `storage` fires in the *other* tabs, so a sign-out elsewhere is reflected
  // here instead of leaving a stale name in the header until a reload.
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

/** Session user for display purposes (name, initials). Authorization is
 * always enforced server-side from the cookie — this is never authoritative. */
export function useSessionUser(): SessionUser | null {
  return useSyncExternalStore(
    subscribeToSessionUser,
    sessionUserSnapshot,
    serverSessionUserSnapshot
  );
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
