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
from prometheus_client import start_http_server

from securgraph import __version__
from securgraph.config import Settings
from securgraph.logging import get_logger

if TYPE_CHECKING:
    pass


_LOG = get_logger(__name__)
_TRACER_PROVIDER: TracerProvider | None = None
_METRICS_SERVER_STARTED = False


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
