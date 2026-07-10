"""Centralized configuration via pydantic-settings.

All runtime knobs go here. No other module reads ``os.environ`` directly.
Values come from `.env` (loaded automatically by Settings) with prefix
``SECURGRAPH_`` unless explicitly aliased.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvName = Literal["dev", "test", "stage", "prod"]

# Known-public default used when no JWT_SECRET is configured. Fine for local
# dev; startup MUST refuse to boot with this value in stage/prod (see
# ``securgraph.api.main``).
DEFAULT_JWT_SECRET = "securgraph-jwt-secret-very-secure-key-default-replace-me"  # noqa: S105


class Settings(BaseSettings):
    """Application settings. Singleton via :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SECURGRAPH_",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # ------------------------------------------------------------------ service
    env: EnvName = "dev"
    service_name: str = "securgraph-api"
    log_level: str = "INFO"

    # ---------------------------------------------------------------------- API
    api_host: str = "0.0.0.0"  # noqa: S104 — dev binding; harden in prod
    api_port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    # ----------------------------------------------------------------- postgres
    # PostgreSQL credentials use bare env names so the same .env feeds both
    # docker-compose AND the app. Override entire DSN via SECURGRAPH_POSTGRES_DSN.
    postgres_user: str = Field(default="securgraph", validation_alias="POSTGRES_USER")
    postgres_password: SecretStr = Field(
        default=SecretStr("securgraph"), validation_alias="POSTGRES_PASSWORD"
    )
    postgres_db: str = Field(default="securgraph", validation_alias="POSTGRES_DB")
    postgres_dsn: str = (
        "postgresql+asyncpg://securgraph_app:securgraph_app@localhost:5432/securgraph"
    )

    # ------------------------------------------------------------------- qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "securgraph_chunks"

    # -------------------------------------------------------------------- neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("securgraph-neo4j")

    # ----------------------------------------------------------------- openfga
    openfga_api_url: str = "http://localhost:8080"
    openfga_store_id: str = ""
    openfga_authorization_model_id: str = ""

    # -------------------------------------------------------------------- redis
    redis_url: str = "redis://localhost:6379/0"

    # ------------------------------------------------------------------- ollama
    ollama_url: str = "http://localhost:11434"
    llm_model: str = "gemma3:1b"
    llm_model_extract: str = "gemma3:1b"
    llm_model_judge: str = "gemma3:1b"
    llm_num_ctx: int = Field(default=4096, ge=512, le=131072)
    llm_thinking: bool = Field(
        default=True,
        description=(
            "Forwarded as the `think` argument to Ollama chat for the online "
            "query/synthesis path (vector_draft, graph_draft, synthesis, "
            "judge_nli, community generator, self healing). Only effective on "
            "models that expose the 'thinking' capability."
        ),
    )
    llm_thinking_extract: bool = Field(
        default=True,
        description=(
            "Separate `think` flag for the offline graph extractor "
            "(extract_graph_slice). Extract benefits from thinking on small "
            "models even when the online query path runs with thinking off, "
            "because JSON-strict schemas are fragile without internal "
            "reasoning. Defaults to True so production extraction is "
            "high-quality unless explicitly disabled."
        ),
    )

    # --------------------------------------------------------------- llm provider
    # LLM-agnostic design (blueprint "Recommended Technology
    # Stack"). The pipeline never imports a vendor SDK directly; it asks
    # `get_llm_adapter()` for an `LLMAdapter` implementation. The default
    # stays `ollama` so sovereign/offline deployments keep working with zero
    # extra dependencies. Per-role overrides allow a mixed deployment, e.g.
    # local Ollama for extraction of sensitive data and a cloud model for the
    # NLI judge over already-authorized evidence (blueprint §5.2).
    llm_provider: Literal["ollama", "openai", "google", "mistral", "anthropic"] = "ollama"
    llm_provider_extract: str = ""
    llm_provider_judge: str = ""
    fallback_policy: Literal["block", "default_provider"] = "block"

    # Cloud provider API keys. Read by the corresponding adapter lazily; if a
    # provider is not configured the key stays empty and that adapter is never
    # instantiated.
    openai_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="OPENAI_API_KEY")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias="OPENAI_BASE_URL",
        description=(
            "OpenAI-compatible endpoint. Set to https://api.mistral.ai/v1 for "
            "the Mistral adapter, or to a local vLLM/LiteLLLM proxy."
        ),
    )
    google_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="GOOGLE_API_KEY")
    anthropic_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="ANTHROPIC_API_KEY"
    )
    mistral_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="MISTRAL_API_KEY")

    # ---------------------------------------------------------------- tokenizer
    # HuggingFace tokenizer used by the chunker for token budgeting. Default is
    # Granite 4.0 H-Tiny (matches the granite4.1:3b Ollama default closely
    # enough; token counts are approximate but stable). First load downloads
    # the tokenizer; subsequent loads read from the HF cache.
    chunker_tokenizer_model: str = "ibm-granite/granite-4.0-h-tiny"

    # --------------------------------------------------------------- embeddings
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: Literal["cpu", "cuda", "mps", "auto"] = "cpu"
    embedding_dim: int = Field(default=1024, ge=1)

    # ------------------------------------------------------------ observability
    otel_enabled: bool = True
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_traces_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    metrics_port: int = Field(default=9464, ge=1, le=65535)

    # ------------------------------------------------------------------- limits
    max_graph_depth: int = Field(default=2, ge=1, le=4)
    max_graph_depth_hard_cap: int = Field(default=4, ge=1, le=8)
    max_evidence_per_query: int = Field(default=20, ge=1, le=200)
    query_token_budget: int = Field(default=3000, ge=256, le=131072)
    consensus_threshold: float = Field(default=0.7, ge=0.0, le=1.0)

    # ----------------------------------------------------------------- features
    # Feature flags that activate as later milestones land. Default OFF; set to
    # True in tests + when each milestone is wired.
    feature_workers_enabled: bool = False  # M6+
    feature_verifier_ensemble: bool = False  # M7+
    audit_tamper_evident: bool = False  # M7+
    feature_self_healing_enabled: bool = False

    # -------------------------------------------------------------- rate limits
    # Requests per minute, enforced in-process (deploy is single-worker
    # uvicorn). 0 disables a limiter; env=test disables all of them.
    ratelimit_login_ip: int = Field(default=10, ge=0)
    ratelimit_login_user: int = Field(default=5, ge=0)
    ratelimit_query: int = Field(default=30, ge=0)
    ratelimit_ingest: int = Field(default=10, ge=0)

    # ------------------------------------------------------------- auth settings
    jwt_secret: SecretStr = Field(
        default=SecretStr(DEFAULT_JWT_SECRET),
        validation_alias="JWT_SECRET",
    )
    auth_enabled: bool = True
    access_token_ttl_minutes: int = Field(default=15, ge=5, le=60)
    refresh_token_ttl_days: int = Field(default=7, ge=1, le=90)
    session_cookie_secure: bool = False
    master_key: SecretStr = SecretStr("")

    # ----------------------------------------------------------- audit integrity
    # HMAC key for tamper-evident audit chaining. Must be set to a random 32+
    # byte hex string and stored *outside* the database (env var, Docker secret,
    # or KMS-injected). If empty, chaining falls back to plain SHA-256 and logs
    # a WARNING — the chain is then NOT secure against an attacker who controls
    # the database.
    audit_hmac_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="AUDIT_HMAC_KEY",
    )
    audit_anchor_s3_bucket: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_ANCHOR_BUCKET",
            "AUDIT_ANCHOR_S3_BUCKET",
            "SECURGRAPH_AUDIT_ANCHOR_BUCKET",
            "SECURGRAPH_AUDIT_ANCHOR_S3_BUCKET",
        ),
    )
    audit_anchor_s3_prefix: str = Field(
        default="securegraph/audit-anchors",
        validation_alias=AliasChoices(
            "AUDIT_ANCHOR_PREFIX",
            "AUDIT_ANCHOR_S3_PREFIX",
            "SECURGRAPH_AUDIT_ANCHOR_PREFIX",
            "SECURGRAPH_AUDIT_ANCHOR_S3_PREFIX",
        ),
    )
    audit_anchor_s3_endpoint: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_ANCHOR_ENDPOINT",
            "AUDIT_ANCHOR_S3_ENDPOINT",
            "SECURGRAPH_AUDIT_ANCHOR_ENDPOINT",
            "SECURGRAPH_AUDIT_ANCHOR_S3_ENDPOINT",
        ),
    )
    audit_anchor_s3_region: str = Field(
        default="us-east-1",
        validation_alias=AliasChoices(
            "AUDIT_ANCHOR_REGION",
            "AUDIT_ANCHOR_S3_REGION",
            "SECURGRAPH_AUDIT_ANCHOR_REGION",
            "SECURGRAPH_AUDIT_ANCHOR_S3_REGION",
        ),
    )
    audit_anchor_s3_access_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUDIT_ANCHOR_ACCESS_KEY",
            "AUDIT_ANCHOR_S3_ACCESS_KEY",
            "SECURGRAPH_AUDIT_ANCHOR_ACCESS_KEY",
            "SECURGRAPH_AUDIT_ANCHOR_S3_ACCESS_KEY",
        ),
    )
    audit_anchor_s3_secret_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "AUDIT_ANCHOR_SECRET_KEY",
            "AUDIT_ANCHOR_S3_SECRET_KEY",
            "SECURGRAPH_AUDIT_ANCHOR_SECRET_KEY",
            "SECURGRAPH_AUDIT_ANCHOR_S3_SECRET_KEY",
        ),
    )
    audit_anchor_retention_days: int = Field(default=365, ge=1, le=36500)
    audit_anchor_interval_seconds: int = Field(default=3600, ge=60, le=86400)
    audit_anchor_event_threshold: int = Field(default=10000, ge=1, le=10_000_000)

    _legacy_anchor_alias_warned: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize_prefixed_anchor_constructor_keys(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        mappings = {
            "SECURGRAPH_AUDIT_ANCHOR_BUCKET": "audit_anchor_s3_bucket",
            "SECURGRAPH_AUDIT_ANCHOR_PREFIX": "audit_anchor_s3_prefix",
            "SECURGRAPH_AUDIT_ANCHOR_ENDPOINT": "audit_anchor_s3_endpoint",
            "SECURGRAPH_AUDIT_ANCHOR_REGION": "audit_anchor_s3_region",
            "SECURGRAPH_AUDIT_ANCHOR_ACCESS_KEY": "audit_anchor_s3_access_key",
            "SECURGRAPH_AUDIT_ANCHOR_SECRET_KEY": "audit_anchor_s3_secret_key",
            "SECURGRAPH_AUDIT_ANCHOR_S3_BUCKET": "audit_anchor_s3_bucket",
            "SECURGRAPH_AUDIT_ANCHOR_S3_PREFIX": "audit_anchor_s3_prefix",
            "SECURGRAPH_AUDIT_ANCHOR_S3_ENDPOINT": "audit_anchor_s3_endpoint",
            "SECURGRAPH_AUDIT_ANCHOR_S3_REGION": "audit_anchor_s3_region",
            "SECURGRAPH_AUDIT_ANCHOR_S3_ACCESS_KEY": "audit_anchor_s3_access_key",
            "SECURGRAPH_AUDIT_ANCHOR_S3_SECRET_KEY": "audit_anchor_s3_secret_key",
        }
        normalized = dict(data)
        for source, target in mappings.items():
            if source in normalized and target not in normalized:
                normalized[target] = normalized.pop(source)
        return normalized

    @computed_field  # type: ignore[prop-decorator]
    @property
    def audit_anchor_bucket(self) -> str:
        return self.audit_anchor_s3_bucket

    @computed_field  # type: ignore[prop-decorator]
    @property
    def audit_anchor_endpoint(self) -> str:
        return self.audit_anchor_s3_endpoint

    @computed_field  # type: ignore[prop-decorator]
    @property
    def audit_anchor_backend(self) -> str:
        if self.audit_anchor_s3_bucket and self.audit_anchor_s3_endpoint:
            return "minio"
        return "local"

    @model_validator(mode="after")
    def _warn_legacy_anchor_env_names(self) -> Settings:
        """Accept deprecated *_S3_* anchor env names; canonical names are preferred."""
        import os

        legacy_pairs = (
            ("SECURGRAPH_AUDIT_ANCHOR_S3_BUCKET", "SECURGRAPH_AUDIT_ANCHOR_BUCKET"),
            ("SECURGRAPH_AUDIT_ANCHOR_S3_ENDPOINT", "SECURGRAPH_AUDIT_ANCHOR_ENDPOINT"),
            ("SECURGRAPH_AUDIT_ANCHOR_S3_ACCESS_KEY", "SECURGRAPH_AUDIT_ANCHOR_ACCESS_KEY"),
            ("SECURGRAPH_AUDIT_ANCHOR_S3_SECRET_KEY", "SECURGRAPH_AUDIT_ANCHOR_SECRET_KEY"),
        )
        if any(os.environ.get(legacy) and not os.environ.get(canonical) for legacy, canonical in legacy_pairs):
            if not self._legacy_anchor_alias_warned:
                import logging

                logging.getLogger(__name__).warning(
                    "Deprecated SECURGRAPH_AUDIT_ANCHOR_S3_* env vars are set; "
                    "prefer SECURGRAPH_AUDIT_ANCHOR_* (S3-compatible MinIO naming)."
                )
                object.__setattr__(self, "_legacy_anchor_alias_warned", True)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Safe to call anywhere; do not mutate the returned obj."""
    return Settings()
