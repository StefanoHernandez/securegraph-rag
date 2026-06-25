import {
  QueryRequest,
  QueryResponse,
  ExplainReport,
  HealthResponse,
  ActiveSession,
  TenantSummary,
  ModelCatalogEntry,
  ProviderCredentialStatus,
  ProviderName,
  RuntimeSetting,
} from "./types";

const API_PREFIX = "/api/v1";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function readCookie(name: string): string | null {
  if (typeof document === "undefined") {
    return null;
  }

  const prefix = `${name}=`;
  const cookie = document.cookie
    .split("; ")
    .find((entry) => entry.startsWith(prefix));
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : null;
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers);
  const method = (options.method ?? "GET").toUpperCase();
  const csrfToken = readCookie("securgraph_csrf");

  if (csrfToken && UNSAFE_METHODS.has(method) && !headers.has("X-CSRF-Token")) {
    headers.set("X-CSRF-Token", csrfToken);
  }

  const url = path.startsWith(API_PREFIX) ? path : `${API_PREFIX}${path}`;

  const response = await fetch(url, {
    ...options,
    headers,
    credentials: "include",
  });

  if (response.status === 401) {
    if (typeof window !== "undefined") {
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

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string, options?: RequestInit) =>
    request<T>(path, { ...options, method: "GET" }),

  post: <T>(path: string, body: unknown, options?: RequestInit) =>
    request<T>(path, {
      ...options,
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
      body: JSON.stringify(body),
    }),

  put: <T>(path: string, body: unknown, options?: RequestInit) =>
    request<T>(path, {
      ...options,
      method: "PUT",
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
      // Let fetch set the multipart/form-data content type + boundary.
    }),
};

// API Endpoints mappings:
export const queryApi = {
  submitQuery: (req: QueryRequest) => api.post<QueryResponse>("/query", req),
};

export const explainApi = {
  getReport: (traceId: string, tenantId: string, _userId?: string) => {
    void _userId;
    return api.get<ExplainReport>(
      `/explain/${traceId}?tenant_id=${encodeURIComponent(
        tenantId
      )}`
    );
  },
};

export const healthApi = {
  getHealth: () => api.get<HealthResponse>("/health"),
};

export interface ActiveConfig {
  llm: { provider: string; model: string };
  extract: { provider: string; model: string };
  judge: { provider: string; model: string };
  embedding: string;
  access_store: string;
  auth_enabled: boolean;
  tamper_evident: boolean;
}

export const configApi = {
  getConfig: () => api.get<ActiveConfig>("/config"),
};

export const tenantApi = {
  listTenants: () => api.get<TenantSummary[]>("/tenants"),
};

export const ingestApi = {
  uploadFile: (formData: FormData) =>
    api.postMultipart<{ job_id: string; document_id: string; trace_id: string; status: string; chunks_created: number }>("/ingest", formData),
  
  getStatus: (jobId: string) =>
    api.get<{
      job_id: string;
      document_id: string;
      tenant_id: string;
      status: "queued" | "running" | "completed" | "failed";
      error?: string | null;
    }>(`/ingest/status/${jobId}`),
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
        agreement_score?: number | null;
        high_trust?: boolean | null;
      }>;
    }>(`/admin/audit/traces?tenant_id=${encodeURIComponent(tenantId)}&page=${page}&size=${size}`),

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
    }>("/admin/audit/verify-chain", { tenant_id: tenantId }),

  listPermissions: () =>
    api.get<{
      tuples: Array<{
        user: string;
        relation: string;
        object: string;
      }>;
    }>("/admin/permissions"),

  grantPermission: (user: string, relation: string, object: string) =>
    api.post<{ status: string; message: string }>("/admin/permissions/grant", {
      user,
      relation,
      object,
    }),

  revokePermission: (user: string, relation: string, object: string) =>
    api.post<{ status: string; message: string }>("/admin/permissions/revoke", {
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
    }>(`/admin/graph/nodes?tenant_id=${encodeURIComponent(tenantId)}`),

  getGraphCommunities: (tenantId: string) =>
    api.get<{
      communities: Array<{
        id: string;
        title: string;
        level: number;
        summary: string;
      }>;
    }>(`/admin/graph/communities?tenant_id=${encodeURIComponent(tenantId)}`),
};

export const authApi = {
  login: (username: string, password: string, tenantId?: string) =>
    api.post<import("./types").UserSession>("/auth/login", {
      username,
      password,
      tenant_id: tenantId || undefined,
    }),

  me: () => api.get<import("./types").SessionUser>("/auth/me"),

  refresh: () => api.post<import("./types").UserSession>("/auth/refresh", {}),

  logout: () => api.post<void>("/auth/logout", {}),

  changePassword: (currentPassword: string, newPassword: string) =>
    api.post<void>("/auth/change-password", {
      current_password: currentPassword,
      new_password: newPassword,
    }),

  listSessions: () => api.get<ActiveSession[]>("/auth/sessions"),

  revokeSession: (sessionId: string) =>
    request<void>(`/api/v1/auth/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    }),
};

export const settingsApi = {
  listProviders: (tenantId: string) =>
    api.get<ProviderCredentialStatus[]>(
      `/tenants/${encodeURIComponent(tenantId)}/settings/providers`
    ),

  saveProviderCredential: (
    tenantId: string,
    provider: ProviderName,
    apiKey: string,
    baseUrl?: string
  ) =>
    api.post<ProviderCredentialStatus>(
      `/tenants/${encodeURIComponent(tenantId)}/settings/providers/${provider}/credential`,
      {
        api_key: apiKey,
        base_url: baseUrl || undefined,
      }
    ),

  listModels: (tenantId: string) =>
    api.get<ModelCatalogEntry[]>(
      `/tenants/${encodeURIComponent(tenantId)}/settings/models`
    ),

  saveRuntimeSetting: (
    tenantId: string,
    key: string,
    value: Record<string, unknown>
  ) =>
    api.put<RuntimeSetting>(
      `/tenants/${encodeURIComponent(tenantId)}/settings/runtime/${encodeURIComponent(key)}`,
      { key, value }
    ),
};
