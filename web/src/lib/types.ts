export type RetrievalMode = "auto" | "vector" | "lexical" | "graph" | "hybrid";
export type QueryType = "standard" | "global";

export interface QueryOptions {
  include_explanation: boolean;
  max_graph_depth: number;
  retrieval_mode: RetrievalMode;
  query_type: QueryType;
  top_k: number;
  consensus_threshold: number;
}

export interface QueryRequest {
  tenant_id: string;
  user_id: string;
  query: string;
  scope_collections?: string[];
  scope_max_classification?: string | null;
  scope_document_ids?: string[];
  options?: Partial<QueryOptions>;
}

export interface ConfidenceBlock {
  high_trust: boolean;
  agreement_score: number | null;
  entity_overlap: number | null;
  claim_nli_score: number | null;
  evidence_coverage: number | null;
}

export interface ChannelSummary {
  evidence_count: number;
  draft_summary: string | null;
  paths: string[];
}

export interface QueryChannels {
  vector?: ChannelSummary | null;
  graph?: ChannelSummary | null;
  lexical?: ChannelSummary | null;
  community?: ChannelSummary | null;
}

export interface Citation {
  evidence_id: string;
  document_id: string | null;
  chunk_id: string | null;
  graph_path_id: string | null;
  label: string;
  source_uri: string | null;
  page_start: number | null;
  page_end: number | null;
}

export interface QueryResponse {
  answer: string;
  citations: Citation[];
  confidence: ConfidenceBlock;
  channels: QueryChannels;
  trace_id: string;
  warnings: string[];
}

export interface ExplainReport {
  trace_id: string;
  query: string;
  answer: string | null;
  started_at: string;
  completed_at: string | null;
  latency_ms: number | null;

  // W3C PROV Lineage
  prov_agents: Array<{
    id: string;
    type: string;
    label: string;
    [key: string]: unknown;
  }>;
  prov_activities: Array<{
    id: string;
    type: string;
    label: string;
    [key: string]: unknown;
  }>;
  prov_entities: Array<{
    id: string;
    type: string;
    label: string;
    [key: string]: unknown;
  }>;

  // Diagnostic overview
  evidence: Array<{
    evidence_id: string;
    retriever_id: string;
    object_id: string;
    original_rank: number;
    original_score: number;
    included_in_prompt: boolean;
    status: "allowed" | "redacted";
    object_data: string;
  }>;
  policy_summary: {
    allowed: number;
    denied: number;
  };

  // Consensus snapshot (C2). Null for traces recorded before consensus was
  // persisted; the timeline renders a neutral "not recorded" state.
  consensus: {
    agreement_score: number | null;
    high_trust: boolean | null;
    entity_overlap: number | null;
    claim_nli_score: number | null;
  } | null;
}

export interface ServiceStatus {
  status: "ok" | "degraded" | "unavailable" | "not_implemented";
  latency_ms: number | null;
  detail?: string | null;
}

export interface HealthResponse {
  status: "ok" | "degraded" | "down";
  service: string;
  version: string;
  env: string;
  components: Record<string, ServiceStatus>;
}

export interface IngestionJob {
  job_id: string;
  document_id: string;
  tenant_id: string;
  status: "queued" | "running" | "completed" | "failed";
  progress?: number;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface SessionMembership {
  tenant_id: string;
  role: string;
}

export interface SessionUser {
  id: string;
  username: string;
  display_name: string;
  email: string | null;
  role: string;
  tenant_id: string | null;
  is_super_admin: boolean;
  must_change_password: boolean;
  memberships: SessionMembership[];
}

export interface UserSession {
  user: SessionUser;
}

export interface TenantSummary {
  id: string;
  display_name: string;
  status: string;
  tenant_admin_settings_enabled: boolean;
}

export interface ActiveSession {
  id: string;
  tenant_id: string | null;
  current: boolean;
  user_agent: string | null;
  ip_address: string | null;
  created_at: string;
  last_seen_at: string;
  expires_at: string | null;
}

export type ProviderName = "openai" | "anthropic" | "gemini" | "mistral" | "ollama";

export interface ProviderCredentialStatus {
  provider: ProviderName;
  tenant_id: string;
  configured: boolean;
  fingerprint: string;
  status: string;
  last_tested_at: string | null;
  last_test_ok: boolean | null;
  models_discovered: number;
}

export interface RuntimeSetting {
  key: string;
  value: Record<string, unknown>;
  restart_required: boolean;
  version: number;
  updated_at: string | null;
}

export interface ModelCatalogEntry {
  provider: ProviderName;
  model_id: string;
  capabilities: string[];
  status: string;
  discovered_at: string | null;
}
