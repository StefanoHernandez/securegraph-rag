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
    [key: string]: any;
  }>;
  prov_activities: Array<{
    id: string;
    type: string;
    label: string;
    [key: string]: any;
  }>;
  prov_entities: Array<{
    id: string;
    type: string;
    label: string;
    [key: string]: any;
  }>;

  // Diagnostic overview
  evidence: Array<{
    evidence_id: string;
    retriever_id: string;
    object_id: string;
    original_rank: number;
    original_score: number;
    fused_rank: number | null;
    fused_score: number | null;
    policy_check_id: string;
    included_in_prompt: boolean;
    text: string | null;
    is_allowed: boolean;
  }>;
  policy_summary: {
    allowed: number;
    denied: number;
  };
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

export interface UserSession {
  token: string;
  user: {
    id: string;
    username: string;
    role: string;
    tenant_id: string;
  };
}
