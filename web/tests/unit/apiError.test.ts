import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "@/lib/api";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ApiError", () => {
  // Matches tests/unit/api.test.ts: restoreMocks in vitest.config.ts does not
  // undo stubGlobal, so the stubbed fetch has to be cleared explicitly.
  afterEach(() => vi.unstubAllGlobals());

  it("carries status, code, detail and request id from the body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(451, {
          detail: "Egress blocked by tenant policy: provider=openai stage=judge",
          code: "egress_blocked",
          request_id: "0f3c8a1e",
        })
      )
    );

    const error = await api.get("/anything").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(451);
    expect(apiError.code).toBe("egress_blocked");
    expect(apiError.requestId).toBe("0f3c8a1e");
  });

  it("falls back to a status-derived code when the body is not JSON", async () => {
    // A proxy 502 returns HTML, not our contract.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("<html>Bad Gateway</html>", { status: 502 }))
    );

    const error = (await api.get("/anything").catch((e: unknown) => e)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("http_502");
    expect(error.requestId).toBeNull();
  });

  it("returns an ApiError even when the body is the JSON literal null", async () => {
    // JSON.parse("null") succeeds, so a try/catch around response.json() does
    // not cover it. Reading a field off the result throws, and request() would
    // reject with a TypeError instead of an ApiError.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("null", { status: 500 }))
    );

    const error = (await api.get("/anything").catch((e: unknown) => e)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("http_500");
    expect(error.detail).toBe("");
    expect(error.requestId).toBeNull();
  });

  it("never carries the raw response body in its message", async () => {
    const sentinel = "SELECT secret_column FROM classified_chunks";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(sentinel, { status: 500 }))
    );

    const error = (await api.get("/anything").catch((e: unknown) => e)) as ApiError;

    expect(error.message).not.toContain(sentinel);
    expect(error.detail).not.toContain(sentinel);
  });
});
