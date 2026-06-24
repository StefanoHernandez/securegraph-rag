import { QueryRequest, QueryResponse, ExplainReport, HealthResponse } from "./types";

// Base API URL configuration.
// By default, we proxy via next.config.ts rewrites under '/api'.
const BASE_URL = typeof window !== "undefined" ? "" : "http://127.0.0.1:8000";

// Direct backend origin used to BYPASS the Next dev rewrite for multipart
// uploads: Next's rewrite proxy mishandles streamed multipart/form-data bodies
// and resets the upstream connection ("socket hang up"). CORS is open in dev
// (allow_origins=*), so the browser can hit the backend directly. Override via
// NEXT_PUBLIC_API_DIRECT in other environments.
const DIRECT_BACKEND =
  process.env.NEXT_PUBLIC_API_DIRECT || "http://127.0.0.1:8000";

async function request<T>(
  path: string,
  options: RequestInit = {},
  direct = false
): Promise<T> {
  const headers = new Headers(options.headers);

  if (typeof window !== "undefined") {
    const token = localStorage.getItem("securgraph_token");
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  // When `direct`, target the backend origin and drop the "/api" proxy prefix
  // (backend routes are mounted without it).
  const url =
    direct && typeof window !== "undefined"
      ? `${DIRECT_BACKEND}${path.replace(/^\/api/, "")}`
      : `${BASE_URL}${path}`;

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    if (typeof window !== "undefined") {
      localStorage.removeItem("securgraph_token");
      localStorage.removeItem("securgraph_user");
      window.location.href = "/login";
    }
    throw new Error("Unauthorized");
  }

  if (!response.ok) {
    const errorBody = await response.text().catch(() => "");
    throw new Error(
      `HTTP Error ${response.status}: ${errorBody || response.statusText}`
    );
  }

  return response.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string, options?: RequestInit) =>
    request<T>(path, { ...options, method: "GET" }),

  post: <T>(path: string, body: any, options?: RequestInit) =>
    request<T>(path, {
      ...options,
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
      body: JSON.stringify(body),
    }),

  postMultipart: <T>(path: string, formData: FormData, options?: RequestInit) =>
    request<T>(
      path,
      {
        ...options,
        method: "POST",
        body: formData,
        // Let fetch set the multipart/form-data content type + boundary.
      },
      // Bypass the Next rewrite proxy — it resets multipart POST bodies.
      true
    ),
};

// API Endpoints mappings:
export const queryApi = {
  submitQuery: (req: QueryRequest) => api.post<QueryResponse>("/api/query", req),
};

export const explainApi = {
  getReport: (traceId: string, tenantId: string, userId: string) =>
    api.get<ExplainReport>(
      `/api/explain/${traceId}?tenant_id=${encodeURIComponent(
        tenantId
      )}&user_id=${encodeURIComponent(userId)}`
    ),
};

export const healthApi = {
  getHealth: () => api.get<HealthResponse>("/api/health"),
};

export const ingestApi = {
  uploadFile: (formData: FormData) =>
    api.postMultipart<{ job_id: string; document_id: string; trace_id: string; status: string; chunks_created: number }>("/api/ingest", formData),
  
  getStatus: (jobId: string) =>
    api.get<{
      job_id: string;
      document_id: string;
      tenant_id: string;
      status: "queued" | "running" | "completed" | "failed";
      error?: string | null;
    }>(`/api/ingest/status/${jobId}`),
};

export const adminApi = {
  getAuditTraces: (tenantId: string, page: number = 1, size: number = 20) =>
    api.get<{
      total: number;
      page: number;
      size: number;
      traces: Array<{
        trace_id: string;
        tenant_id: string;
        user_id: string;
        query: string;
        answer: string | null;
        started_at: string;
        completed_at: string | null;
        latency_ms: number | null;
        tamper_hash: string | null;
      }>;
    }>(`/api/admin/audit/traces?tenant_id=${encodeURIComponent(tenantId)}&page=${page}&size=${size}`),

  verifyAuditChain: (tenantId: string) =>
    api.post<{
      tenant_id: string;
      is_intact: boolean;
      tables: Record<
        string,
        {
          is_intact: boolean;
          tampered_count: number;
          tampered_ids: string[];
        }
      >;
    }>("/api/admin/audit/verify-chain", { tenant_id: tenantId }),

  listPermissions: () =>
    api.get<{
      tuples: Array<{
        user: string;
        relation: string;
        object: string;
      }>;
    }>("/api/admin/permissions"),

  grantPermission: (user: string, relation: string, object: string) =>
    api.post<{ status: string; message: string }>("/api/admin/permissions/grant", {
      user,
      relation,
      object,
    }),

  revokePermission: (user: string, relation: string, object: string) =>
    api.post<{ status: string; message: string }>("/api/admin/permissions/revoke", {
      user,
      relation,
      object,
    }),

  getGraphNodes: (tenantId: string) =>
    api.get<{
      nodes: Array<{
        id: string;
        type: string;
        description: string;
        classification: string;
      }>;
      links: Array<{
        source: string;
        target: string;
        type: string;
        description: string;
        confidence: number;
        classification: string;
      }>;
    }>(`/api/admin/graph/nodes?tenant_id=${encodeURIComponent(tenantId)}`),

  getGraphCommunities: (tenantId: string) =>
    api.get<{
      communities: Array<{
        id: string;
        title: string;
        level: number;
        summary: string;
      }>;
    }>(`/api/admin/graph/communities?tenant_id=${encodeURIComponent(tenantId)}`),
};

