import { afterEach, describe, expect, it, vi } from "vitest";
import { operationsApi, queryApi } from "@/lib/api";
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

    await expect(operationsApi.listIngestionJobs("tenant/a")).rejects.toThrow(
      "HTTP Error 503: worker unavailable"
    );
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/tenants/tenant%2Fa/ingest-jobs");
  });
});
