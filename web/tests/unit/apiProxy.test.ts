import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "@/app/api/v1/[...path]/route";

describe("API proxy", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("returns a JSON 503 when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("connection refused")));
    const request = new NextRequest("http://localhost/api/v1/auth/me");

    const response = await GET(request, {
      params: Promise.resolve({ path: ["auth", "me"] }),
    });

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toEqual({ detail: "Backend service unavailable." });
  });
});
