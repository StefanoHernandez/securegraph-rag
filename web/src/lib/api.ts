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
  AccessTemplate,
  Classification,
  GroupKind,
  TenantDetail,
  TenantGroup,
  TenantRole,
  TenantUser,
  TenantUserCreateResponse,
  TemporaryPasswordResponse,
  UserSearchResult,
  OperationIngestionJob,
  OperationIngestionJobList,
  OperationJob,
  OperationJobList,
  CostEstimateRequest,
  CostEstimateResponse,
  EmbeddingReindexPlan,
} from "./types";
import { clearSession } from "./session";

const API_PREFIX = "/api/v1";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

type ApiRequestOptions = RequestInit & {
  redirectOnUnauthorized?: boolean;
};

// Single-flight refresh: concurrent 401s share one /auth/refresh call
// instead of racing several token rotations.
let refreshPromise: Promise<boolean> | null = null;

async function tryRefreshSession(): Promise<boolean> {
  if (!refreshPromise) {
    // The refresh cookie alone makes this a cookie-authenticated POST, so the
    // CSRF middleware rejects it without the double-submit header. It must not
    // be exempted instead: a forced cross-site refresh would rotate the token
    // out from under the real client, and the next legitimate use would look
    // like replay and revoke the whole session family.
    const csrfToken = readCookie("securgraph_csrf");
    refreshPromise = fetch(`${API_PREFIX}/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: csrfToken ? { "X-CSRF-Token": csrfToken } : undefined,
    })
      .then((res) => res.ok)
      .catch(() => false)
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

function redirectToLogin(): void {
  clearSession();
  const path = window.location.pathname + window.location.search;
  const next =
    path && path !== "/login" ? `?next=${encodeURIComponent(path)}` : "";
  window.location.href = `/login${next}`;
}

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

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly detail: string,
    readonly requestId: string | null
  ) {
    super(detail || `Request failed with status ${status}`);
    this.name = "ApiError";
  }
}

type ErrorPayload = { detail?: unknown; code?: unknown; request_id?: unknown };

async function toApiError(response: Response): Promise<ApiError> {
  // A body that is not our contract — a proxy's HTML 502, an empty 500 — must
  // not become the user-visible message. Only the parsed fields are trusted.
  let payload: ErrorPayload = {};
  try {
    payload = (await response.json()) as ErrorPayload;
  } catch {
    payload = {};
  }
  const code = typeof payload.code === "string" ? payload.code : `http_${response.status}`;
  const detail = typeof payload.detail === "string" ? payload.detail : "";
  const requestId = typeof payload.request_id === "string" ? payload.request_id : null;
  return new ApiError(response.status, code, detail, requestId);
}

async function request<T>(
  path: string,
  options: ApiRequestOptions = {},
  isRetryAfterRefresh = false
): Promise<T> {
  const { redirectOnUnauthorized = true, ...fetchOptions } = options;
  const headers = new Headers(options.headers);
  const method = (options.method ?? "GET").toUpperCase();
  const csrfToken = readCookie("securgraph_csrf");

  if (csrfToken && UNSAFE_METHODS.has(method) && !headers.has("X-CSRF-Token")) {
    headers.set("X-CSRF-Token", csrfToken);
  }

  const url = path.startsWith(API_PREFIX) ? path : `${API_PREFIX}${path}`;

  const response = await fetch(url, {
    ...fetchOptions,
    headers,
    credentials: "include",
  });

  if (response.status === 401) {
    // Expired access token with a valid refresh cookie: refresh once and
    // replay the request transparently. Never for auth endpoints themselves
    // and never twice (anti-loop).
    const isAuthEndpoint = url.startsWith(`${API_PREFIX}/auth/`);
    if (!isAuthEndpoint && !isRetryAfterRefresh && typeof window !== "undefined") {
      const refreshed = await tryRefreshSession();
      if (refreshed) {
        return request<T>(path, options, true);
      }
    }
    if (redirectOnUnauthorized && typeof window !== "undefined") {
      redirectToLogin();
    }
    throw new Error("Unauthorized");
  }

  if (!response.ok) {
    throw await toApiError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string, options?: ApiRequestOptions) =>
    request<T>(path, { ...options, method: "GET" }),

  post: <T>(path: string, body: unknown, options?: ApiRequestOptions) =>
    request<T>(path, {
      ...options,
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
      body: JSON.stringify(body),
    }),

  put: <T>(path: string, body: unknown, options?: ApiRequestOptions) =>
    request<T>(path, {
      ...options,
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
      body: JSON.stringify(body),
    }),

  patch: <T>(path: string, body: unknown, options?: ApiRequestOptions) =>
    request<T>(path, {
      ...options,
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
      body: JSON.stringify(body),
    }),

  delete: <T>(path: string, options?: ApiRequestOptions) =>
    request<T>(path, { ...options, method: "DELETE" }),

  postMultipart: <T>(
    path: string,
    formData: FormData,
    options?: ApiRequestOptions
  ) =>
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
  // /health is deprecated; /health/ready returns the identical payload.
  getHealth: () => api.get<HealthResponse>("/health/ready"),
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

export const controlPlaneApi = {
  listTenants: () => api.get<TenantDetail[]>("/tenants"),

  createTenant: (id: string, displayName: string) =>
    api.post<TenantDetail>("/tenants", {
      id,
      display_name: displayName,
    }),

  updateTenant: (
    tenantId: string,
    body: {
      display_name?: string;
      status?: "active" | "suspended" | "archived";
      tenant_admin_settings_enabled?: boolean;
    }
  ) => api.patch<TenantDetail>(`/tenants/${encodeURIComponent(tenantId)}`, body),

  listUsers: (tenantId: string) =>
    api.get<TenantUser[]>(`/tenants/${encodeURIComponent(tenantId)}/users`),

  searchUsers: (query: string) =>
    api.get<UserSearchResult[]>(`/users/search?q=${encodeURIComponent(query)}`),

  createUser: (
    tenantId: string,
    body: {
      username: string;
      display_name: string;
      email?: string | null;
      role: TenantRole;
    }
  ) =>
    api.post<TenantUserCreateResponse>(
      `/tenants/${encodeURIComponent(tenantId)}/users`,
      body
    ),

  updateUserMembership: (
    tenantId: string,
    userId: string,
    body: { role?: TenantRole; is_active?: boolean }
  ) =>
    api.patch<TenantUser>(
      `/tenants/${encodeURIComponent(tenantId)}/users/${encodeURIComponent(userId)}`,
      body
    ),

  resetPassword: (tenantId: string, userId: string) =>
    api.post<TemporaryPasswordResponse>(
      `/tenants/${encodeURIComponent(tenantId)}/users/${encodeURIComponent(userId)}/reset-password`,
      {}
    ),

  attachExistingUser: (tenantId: string, userId: string, role: TenantRole) =>
    api.post<TenantUser>(
      `/tenants/${encodeURIComponent(tenantId)}/users/${encodeURIComponent(userId)}/membership`,
      { role }
    ),

  listGroups: (tenantId: string) =>
    api.get<TenantGroup[]>(`/tenants/${encodeURIComponent(tenantId)}/groups`),

  createGroup: (
    tenantId: string,
    body: {
      slug: string;
      display_name: string;
      kind: GroupKind;
      description?: string | null;
    }
  ) =>
    api.post<TenantGroup>(
      `/tenants/${encodeURIComponent(tenantId)}/groups`,
      body
    ),

  updateGroup: (
    tenantId: string,
    groupId: string,
    body: {
      display_name?: string;
      kind?: GroupKind;
      description?: string | null;
      is_active?: boolean;
    }
  ) =>
    api.patch<TenantGroup>(
      `/tenants/${encodeURIComponent(tenantId)}/groups/${encodeURIComponent(groupId)}`,
      body
    ),

  listGroupMembers: (tenantId: string, groupId: string) =>
    api.get<TenantUser[]>(
      `/tenants/${encodeURIComponent(tenantId)}/groups/${encodeURIComponent(groupId)}/members`
    ),

  addGroupMember: (tenantId: string, groupId: string, userId: string) =>
    api.put<void>(
      `/tenants/${encodeURIComponent(tenantId)}/groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(userId)}`,
      {}
    ),

  removeGroupMember: (tenantId: string, groupId: string, userId: string) =>
    api.delete<void>(
      `/tenants/${encodeURIComponent(tenantId)}/groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(userId)}`
    ),

  listAccessTemplates: (tenantId: string) =>
    api.get<AccessTemplate[]>(
      `/tenants/${encodeURIComponent(tenantId)}/access-templates`
    ),

  createAccessTemplate: (
    tenantId: string,
    body: {
      document_type: string;
      display_name: string;
      default_classification: Classification;
      owner_rule: "uploader" | "tenant_admin";
      allowed_group_ids: string[];
      direct_viewer_ids: string[];
    }
  ) =>
    api.post<AccessTemplate>(
      `/tenants/${encodeURIComponent(tenantId)}/access-templates`,
      body
    ),

  updateAccessTemplate: (
    tenantId: string,
    templateId: string,
    body: {
      display_name?: string;
      default_classification?: Classification;
      owner_rule?: "uploader" | "tenant_admin";
      allowed_group_ids?: string[];
      direct_viewer_ids?: string[];
      is_active?: boolean;
    }
  ) =>
    api.patch<AccessTemplate>(
      `/tenants/${encodeURIComponent(tenantId)}/access-templates/${encodeURIComponent(templateId)}`,
      body
    ),
};

export const ingestApi = {
  uploadFile: (formData: FormData) =>
    api.postMultipart<{ job_id: string; document_id: string; trace_id: string; status: string; chunks_created: number }>("/ingest", formData),
  
  getStatus: (jobId: string) =>
    api.get<{
      job_id: string;
      document_id: string;
      tenant_id: string;
      status: "queued" | "running" | "completed" | "failed" | "reconcile_required";
      error?: string | null;
    }>(`/ingest/status/${jobId}`),
};

export const operationsApi = {
  listIngestionJobs: (tenantId: string) =>
    api.get<OperationIngestionJobList>(
      `/tenants/${encodeURIComponent(tenantId)}/ingest-jobs`
    ),

  retryIngestionJob: (tenantId: string, jobId: string) =>
    api.post<OperationIngestionJob>(
      `/tenants/${encodeURIComponent(tenantId)}/ingest-jobs/${encodeURIComponent(jobId)}/retry`,
      {}
    ),

  reconcileIngestionJobs: (tenantId: string) =>
    api.post<{ tenant_id: string; reconciled_job_ids: string[] }>(
      `/tenants/${encodeURIComponent(tenantId)}/ingest-jobs/reconcile`,
      {}
    ),

  listOperationJobs: (tenantId: string) =>
    api.get<OperationJobList>(
      `/tenants/${encodeURIComponent(tenantId)}/operation-jobs`
    ),

  verifyLedger: (tenantId: string) =>
    api.post<OperationJob>(
      `/tenants/${encodeURIComponent(tenantId)}/operation-jobs/ledger-verify`,
      {}
    ),

  anchorLedger: (tenantId: string) =>
    api.post<OperationJob>(
      `/tenants/${encodeURIComponent(tenantId)}/operation-jobs/ledger-anchor`,
      {}
    ),

  anchorLedgerIfDue: (tenantId: string) =>
    api.post<OperationJob>(
      `/tenants/${encodeURIComponent(tenantId)}/operation-jobs/ledger-anchor-due`,
      {}
    ),

  recomputeCommunities: (tenantId: string) =>
    api.post<OperationJob>(
      `/tenants/${encodeURIComponent(tenantId)}/operation-jobs/community-recompute`,
      {}
    ),

  runEvalBaseline: (
    tenantId: string,
    body: { corpus_path?: string; timeout_seconds?: number } = {}
  ) =>
    api.post<OperationJob>(
      `/tenants/${encodeURIComponent(tenantId)}/operation-jobs/eval-baseline`,
      body
    ),
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

  backfillAuditChain: (tenantId: string) =>
    api.post<{ tenant_id: string; backfilled: Record<string, number> }>(
      "/admin/audit/backfill-chain",
      { tenant_id: tenantId }
    ),

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

  getVectorAtlas: (
    tenantId: string,
    opts?: { documentId?: string; classification?: string; limit?: number; dims?: 2 | 3 }
  ) => {
    const params = new URLSearchParams({ tenant_id: tenantId });
    if (opts?.documentId) params.set("document_id", opts.documentId);
    if (opts?.classification) params.set("classification", opts.classification);
    if (opts?.limit) params.set("limit", String(opts.limit));
    if (opts?.dims) params.set("dims", String(opts.dims));
    return api.get<{
      tenant_id: string;
      total: number;
      dims: number;
      projection_method: string;
      points: Array<{
        id: string;
        x: number;
        y: number | null;
        z: number | null;
        chunk_id: string;
        document_id: string;
        classification: string;
        text_preview: string;
        source_uri?: string | null;
        last_trace_id?: string | null;
      }>;
    }>(`/admin/vector-atlas?${params.toString()}`);
  },

  searchVectorAtlasNeighbors: (tenantId: string, query: string, limit = 10) =>
    api.post<{
      tenant_id: string;
      query: string;
      chunk_ids: string[];
      scores: number[];
      point_ids: string[];
    }>("/admin/vector-atlas/neighbors", {
      tenant_id: tenantId,
      query,
      limit,
    }),
};

export const authApi = {
  login: (username: string, password: string, tenantId?: string) =>
    api.post<import("./types").UserSession>("/auth/login", {
      username,
      password,
      tenant_id: tenantId || undefined,
    }, { redirectOnUnauthorized: false }),

  me: () =>
    api.get<import("./types").SessionUser>("/auth/me", {
      redirectOnUnauthorized: false,
    }),

  refresh: () =>
    api.post<import("./types").UserSession>(
      "/auth/refresh",
      {},
      { redirectOnUnauthorized: false }
    ),

  logout: () => api.post<void>("/auth/logout", {}, { redirectOnUnauthorized: false }),

  changePassword: (currentPassword: string, newPassword: string) =>
    api.post<void>("/auth/change-password", {
      current_password: currentPassword,
      new_password: newPassword,
    }, { redirectOnUnauthorized: false }),

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

  testProviderCredential: (tenantId: string, provider: ProviderName) =>
    api.post<ProviderCredentialStatus>(
      `/tenants/${encodeURIComponent(tenantId)}/settings/providers/${provider}/credential/test`,
      {}
    ),

  deleteProviderCredential: (tenantId: string, provider: ProviderName) =>
    api.delete<void>(
      `/tenants/${encodeURIComponent(tenantId)}/settings/providers/${provider}/credential`
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

  estimateCost: (tenantId: string, payload: CostEstimateRequest) =>
    api.post<CostEstimateResponse>(
      `/tenants/${encodeURIComponent(tenantId)}/settings/cost-estimate`,
      payload
    ),

  planEmbeddingReindex: (
    tenantId: string,
    payload: { to_model: string; from_model?: string; embedding_dim?: number }
  ) =>
    api.post<EmbeddingReindexPlan>(
      `/tenants/${encodeURIComponent(tenantId)}/settings/embedding-reindex/plan`,
      payload
    ),
};
