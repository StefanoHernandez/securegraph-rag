"""Centralized configuration via pydantic-settings.

All runtime knobs go here. No other module reads ``os.environ`` directly.
Values come from `.env` (loaded automatically by Settings) with prefix
``SECURGRAPH_`` unless explicitly aliased.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvName = Literal["dev", "test", "stage", "prod"]


class Settings(BaseSettings):
    """Application settings. Singleton via :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SECURGRAPH_",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ service
    env: EnvName = "dev"
    service_name: str = "securgraph-api"
    log_level: str = "INFO"

    # ---------------------------------------------------------------------- API
    api_host: str = "0.0.0.0"  # noqa: S104 — dev binding; harden in prod
    api_port: int = Field(default=8000, ge=1, le=65535)

    # ----------------------------------------------------------------- postgres
    # PostgreSQL credentials use bare env names so the same .env feeds both
    # docker-compose AND the app. Override entire DSN via SECURGRAPH_POSTGRES_DSN.
    postgres_user: str = Field(default="securgraph", validation_alias="POSTGRES_USER")
    postgres_password: SecretStr = Field(
        default=SecretStr("securgraph"), validation_alias="POSTGRES_PASSWORD"
    )
    postgres_db: str = Field(default="securgraph", validation_alias="POSTGRES_DB")
    postgres_dsn: str = "postgresql+asyncpg://securgraph:securgraph@localhost:5432/securgraph"

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

    # ----------------------------------------------------------------- features
    # Feature flags that activate as later milestones land. Default OFF; set to
    # True in tests + when each milestone is wired.
    feature_workers_enabled: bool = False        # M6+
    feature_communities_enabled: bool = False    # M6+
    feature_verifier_ensemble: bool = False      # M7+
    audit_tamper_evident: bool = False           # M7+


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Safe to call anywhere; do not mutate the returned obj."""
    return Settings()
