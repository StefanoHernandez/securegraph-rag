"""OpenTelemetry bootstrap.

Wires traces (OTLP gRPC → Jaeger by default) and a minimal Prometheus metrics
endpoint. Instrumentation libraries (FastAPI, httpx, asyncpg) hook into the
configured TracerProvider automatically.

This module is import-safe: importing it does nothing until ``setup_observability``
is called from the API or worker entry point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from prometheus_client import Counter, Histogram, start_http_server

from securgraph import __version__
from securgraph.config import Settings
from securgraph.logging import get_logger

if TYPE_CHECKING:
    pass


_LOG = get_logger(__name__)
_TRACER_PROVIDER: TracerProvider | None = None
_METRICS_SERVER_STARTED = False


# ---------------------------------------------------------------------------
# Prometheus Global Metrics
# ---------------------------------------------------------------------------

QUERY_COUNT = Counter(
    "securgraph_query_total",
    "Total number of executed secure queries",
    labelnames=["tenant_id", "status"],
)

QUERY_LATENCY_SECS = Histogram(
    "securgraph_query_latency_seconds",
    "Query pipeline latency in seconds",
    labelnames=["tenant_id"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, float("inf")),
)

CONSENSUS_AGREEMENT = Histogram(
    "securgraph_consensus_agreement_score",
    "Cross-retriever consensus agreement score",
    labelnames=["tenant_id"],
    buckets=(0.0, 0.2, 0.4, 0.6, 0.7, 0.8, 0.9, 1.0),
)

POLICY_DECISION_COUNT = Counter(
    "securgraph_policy_decision_total",
    "Total number of ReBAC policy decisions evaluated",
    labelnames=["tenant_id", "object_type", "decision"],
)

GRAPH_SELF_HEALING_COUNT = Counter(
    "securgraph_graph_self_healing_total",
    "Total number of automatic graph self-healing actions taken",
    labelnames=["tenant_id", "action"],
)


def get_tracer() -> trace.Tracer:
    """Return the global process-wide tracer."""
    return trace.get_tracer("securgraph")


def setup_observability(settings: Settings) -> None:
    """Configure tracing and start the Prometheus metrics endpoint.

    Idempotent: safe to call from FastAPI lifespan and from worker startup.
    """
    global _TRACER_PROVIDER, _METRICS_SERVER_STARTED

    if not settings.otel_enabled:
        _LOG.info("otel_disabled")
        return

    if _TRACER_PROVIDER is None:
        resource = Resource.create(
            {
                SERVICE_NAME: settings.service_name,
                SERVICE_VERSION: __version__,
                "securgraph.env": settings.env,
            }
        )
        sampler = ParentBased(root=TraceIdRatioBased(settings.otel_traces_sample_ratio))
        provider = TracerProvider(resource=resource, sampler=sampler)
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
            )
        )
        trace.set_tracer_provider(provider)
        _TRACER_PROVIDER = provider

        # Configure automatic OTel instrumentations
        try:
            from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().instrument()
            AsyncPGInstrumentor().instrument()
            _LOG.info("otel_automatic_instrumentations_configured")
        except Exception as exc:
            _LOG.warning("otel_automatic_instrumentations_failed", error=str(exc))
        _LOG.info(
            "otel_traces_configured",
            endpoint=settings.otel_exporter_otlp_endpoint,
            sample_ratio=settings.otel_traces_sample_ratio,
        )

    if not _METRICS_SERVER_STARTED:
        try:
            start_http_server(settings.metrics_port)
            _METRICS_SERVER_STARTED = True
            _LOG.info("prometheus_metrics_started", port=settings.metrics_port)
        except OSError as exc:
            # Port already bound (e.g. tests, multiple workers). Don't crash.
            _LOG.warning("prometheus_metrics_bind_failed", error=str(exc))


def shutdown_observability() -> None:
    """Flush spans and tear down the provider. Call at process exit."""
    global _TRACER_PROVIDER
    if _TRACER_PROVIDER is not None:
        _TRACER_PROVIDER.shutdown()
        _TRACER_PROVIDER = None
        _LOG.info("otel_shutdown")
