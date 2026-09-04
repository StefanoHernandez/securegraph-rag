import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, authApi, operationsApi, queryApi } from "@/lib/api";
import type { QueryResponse } from "@/lib/types";

const response: QueryResponse = {
  answer: "Authorized answer",
  citations: [],
  confidence: {
    high_trust: false,
    agreement_score: null,
    entity_overlap: null,
    claim_nli_score: null,
    evidence_coverage: null,
  },
  channels: {},
  trace_id: "trace-1",
  warnings: [],
  explanation: null,
};

describe("frontend API contracts", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends only backend-supported classification values in a scoped query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(response), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await queryApi.submitQuery({
      tenant_id: "tenant/a",
      user_id: "user:operator",
      query: "Show restricted evidence",
      scope_max_classification: "restricted",
      scope_collections: ["security"],
      scope_document_ids: ["doc-1"],
      options: { include_explanation: true },
    });

    const [, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(request.method).toBe("POST");
    expect(JSON.parse(String(request.body))).toMatchObject({
      scope_max_classification: "restricted",
      scope_collections: ["security"],
      scope_document_ids: ["doc-1"],
    });
  });

  it("uses the tenant-scoped durable ingestion-jobs endpoint and preserves failures", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("worker unavailable", { status: 503, statusText: "Service Unavailable" })
    );
    vi.stubGlobal("fetch", fetchMock);

    // A failure still throws — but the raw body no longer rides in the
    // message. It used to, and pages render that message straight into their
    // error banner, so a 500's body reached the screen.
    const error = await operationsApi
      .listIngestionJobs("tenant/a")
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect((error as ApiError).code).toBe("http_503");
    expect((error as ApiError).message).not.toContain("worker unavailable");
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/tenants/tenant%2Fa/ingest-jobs");
  });

  it("throws an ApiError on 401 like every other failing status", async () => {
    // The 401 branch used to throw a bare Error, so useErrorMessage saw an
    // unknown value and dropped the code and the request id on the floor.
    // /auth/me is the clean probe: an auth endpoint never triggers the refresh
    // replay, and it opts out of the redirect.
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: "Session is no longer valid",
          code: "unauthorized",
          request_id: "req-401",
        }),
        { status: 401, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const error = await authApi.me().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect((error as ApiError).code).toBe("unauthorized");
    expect((error as ApiError).requestId).toBe("req-401");
  });
});
