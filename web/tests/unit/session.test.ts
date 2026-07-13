import { beforeEach, describe, expect, it } from "vitest";
import { clearSession } from "@/lib/session";

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
