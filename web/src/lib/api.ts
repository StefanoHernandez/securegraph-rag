import { QueryRequest, QueryResponse, ExplainReport, HealthResponse } from "./types";

// Base API URL configuration.
// By default, we proxy via next.config.ts rewrites under '/api'
const BASE_URL = typeof window !== "undefined" ? "" : "http://localhost:8000";

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers);
  
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("securgraph_token");
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  const response = await fetch(`${BASE_URL}${path}`, {
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
    request<T>(path, {
      ...options,
      method: "POST",
      body: formData,
      // Note: Let fetch set the correct multipart/form-data content type + boundary
    }),
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
    api.postMultipart<{ job_id: string; document_id: string }>("/api/ingest", formData),
  
  getStatus: (jobId: string) =>
    api.get<{
      job_id: string;
      document_id: string;
      tenant_id: string;
      status: "queued" | "running" | "completed" | "failed";
      error?: string | null;
    }>(`/api/ingest/status/${jobId}`),
};
