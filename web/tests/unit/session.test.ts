import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { clearSession, useSessionUser } from "@/lib/session";

describe("clearSession", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it("clears application state without removing the selected locale", () => {
    localStorage.setItem("securgraph_user", "operator");
    localStorage.setItem("securgraph_tenant", "tenant-a");
    sessionStorage.setItem("securgraph_query_cache", "stale");
    localStorage.setItem("NEXT_LOCALE", "it");

    clearSession();

    expect(localStorage.getItem("securgraph_user")).toBeNull();
    expect(localStorage.getItem("securgraph_tenant")).toBeNull();
    expect(sessionStorage.getItem("securgraph_query_cache")).toBeNull();
    expect(localStorage.getItem("NEXT_LOCALE")).toBe("it");
  });
});

describe("useSessionUser", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  function stored(user: Record<string, unknown>) {
    localStorage.setItem("securgraph_user", JSON.stringify(user));
  }

  it("returns the stored user after mount", async () => {
    stored({ username: "alice", display_name: "Alice Example" });

    const { result } = renderHook(() => useSessionUser());

    await waitFor(() => expect(result.current?.username).toBe("alice"));
  });

  it("returns null when nothing is stored", () => {
    const { result } = renderHook(() => useSessionUser());
    expect(result.current).toBeNull();
  });

  it("returns null when the stored value is corrupt", () => {
    localStorage.setItem("securgraph_user", "{not json");
    const { result } = renderHook(() => useSessionUser());
    expect(result.current).toBeNull();
  });

  it("keeps a stable reference across re-renders", () => {
    // useSyncExternalStore compares snapshots by identity: parsing afresh on
    // every read would loop forever.
    stored({ username: "alice" });

    const { result, rerender } = renderHook(() => useSessionUser());
    const first = result.current;
    rerender();

    expect(result.current).toBe(first);
  });

  it("picks up a sign-out that happened in another tab", async () => {
    stored({ username: "alice" });
    const { result } = renderHook(() => useSessionUser());
    await waitFor(() => expect(result.current?.username).toBe("alice"));

    act(() => {
      localStorage.removeItem("securgraph_user");
      window.dispatchEvent(new StorageEvent("storage", { key: "securgraph_user" }));
    });

    await waitFor(() => expect(result.current).toBeNull());
  });
});
