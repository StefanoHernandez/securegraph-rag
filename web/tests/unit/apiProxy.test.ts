import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "@/app/api/v1/[...path]/route";

describe("API proxy", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.SECURGRAPH_TRUST_PROXY_HEADERS;
  });

  function captureForwardedHeaders(): () => Headers {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    return () => (fetchMock.mock.calls[0][1] as RequestInit).headers as Headers;
  }

  it("returns a JSON 503 when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("connection refused")));
    const request = new NextRequest("http://localhost/api/v1/auth/me");

    const response = await GET(request, {
      params: Promise.resolve({ path: ["auth", "me"] }),
    });

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toEqual({ detail: "Backend service unavailable." });
  });

  // The backend decides whether to believe X-Forwarded-For, but it can only
  // do that safely if the value reaching it was not written by the browser.
  // Next exposes no peer address in a route handler, so the proxy cannot
  // append a real one — the only safe default is to drop what the caller
  // claims and let the backend fall back to the peer address.
  it("strips caller-supplied forwarding headers by default", async () => {
    const forwardedHeaders = captureForwardedHeaders();
    const request = new NextRequest("http://localhost/api/v1/auth/me", {
      headers: {
        "x-forwarded-for": "203.0.113.7",
        "x-real-ip": "203.0.113.7",
        forwarded: "for=203.0.113.7",
        "x-csrf-token": "keep-me",
      },
    });

    await GET(request, { params: Promise.resolve({ path: ["auth", "me"] }) });

    const headers = forwardedHeaders();
    expect(headers.get("x-forwarded-for")).toBeNull();
    expect(headers.get("x-real-ip")).toBeNull();
    expect(headers.get("forwarded")).toBeNull();
    expect(headers.get("x-csrf-token")).toBe("keep-me");
  });

  // `new URL()` normalises the joined path, so a traversal segment escapes the
  // /api/v1 prefix the proxy is supposed to confine callers to.
  it.each([["..", "auth"], ["auth", ".."], ["%2e%2e", "auth"]])(
    "refuses traversal segments (%s/%s)",
    async (...segments: string[]) => {
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);
      const request = new NextRequest("http://localhost/api/v1/x");

      const response = await GET(request, {
        params: Promise.resolve({ path: segments }),
      });

      expect(response.status).toBe(400);
      expect(fetchMock).not.toHaveBeenCalled();
    }
  );

  it("does not hang when the backend never answers", async () => {
    // Without a timeout an unresponsive backend holds a Next worker open for
    // as long as the socket stays alive.
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: unknown, init: RequestInit) => {
        expect(init.signal).toBeInstanceOf(AbortSignal);
        return Promise.resolve(new Response(null, { status: 204 }));
      })
    );
    const request = new NextRequest("http://localhost/api/v1/auth/me");

    const response = await GET(request, {
      params: Promise.resolve({ path: ["auth", "me"] }),
    });

    expect(response.status).toBe(204);
  });

  it("reports an upstream timeout as 504", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(
        Object.assign(new Error("The operation was aborted"), { name: "TimeoutError" })
      )
    );
    const request = new NextRequest("http://localhost/api/v1/auth/me");

    const response = await GET(request, {
      params: Promise.resolve({ path: ["auth", "me"] }),
    });

    expect(response.status).toBe(504);
  });

  it("forwards them when a trusted reverse proxy sits in front", async () => {
    process.env.SECURGRAPH_TRUST_PROXY_HEADERS = "true";
    const forwardedHeaders = captureForwardedHeaders();
    const request = new NextRequest("http://localhost/api/v1/auth/me", {
      headers: { "x-forwarded-for": "203.0.113.7" },
    });

    await GET(request, { params: Promise.resolve({ path: ["auth", "me"] }) });

    expect(forwardedHeaders().get("x-forwarded-for")).toBe("203.0.113.7");
  });
});
