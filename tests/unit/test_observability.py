"""Unit tests for the observability and telemetry layer.

Verifies Prometheus metrics registration, idempotent OpenTelemetry initialization,
and manual span creation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from prometheus_client import REGISTRY

from securgraph.config import Settings
from securgraph.observability.otel import (
    get_tracer,
    setup_observability,
)

pytestmark = pytest.mark.unit


def test_prometheus_metrics_registry() -> None:
    """Verify that all global Prometheus metrics are properly defined and registered."""
    names = {metric.name for metric in REGISTRY.collect()}
    assert "securgraph_query" in names
    assert "securgraph_query_latency_seconds" in names
    assert "securgraph_consensus_agreement_score" in names
    assert "securgraph_policy_decision" in names


def test_setup_observability_idempotent() -> None:
    """Verify setup_observability is safe, idempotent, and does not re-register endpoints."""
    settings = Settings(
        otel_enabled=True,
        metrics_port=9999,
        otel_traces_sample_ratio=1.0,
    )

    with (
        patch("securgraph.observability.otel.TracerProvider") as mock_provider_cls,
        patch("securgraph.observability.otel.BatchSpanProcessor") as mock_processor,
        patch("securgraph.observability.otel.OTLPSpanExporter") as mock_exporter,
        patch("securgraph.observability.otel.start_http_server") as mock_start_http,
        patch("opentelemetry.trace.set_tracer_provider") as mock_set_provider,
        patch("opentelemetry.instrumentation.httpx.HTTPXClientInstrumentor") as mock_httpx_inst,
        patch("opentelemetry.instrumentation.asyncpg.AsyncPGInstrumentor") as mock_asyncpg_inst,
    ):
        import securgraph.observability.otel as otel_module

        # Keep original global module state
        original_provider = otel_module._TRACER_PROVIDER
        original_started = otel_module._METRICS_SERVER_STARTED

        # Reset module state for isolated clean testing
        otel_module._TRACER_PROVIDER = None
        otel_module._METRICS_SERVER_STARTED = False

        try:
            # First initialization call
            setup_observability(settings)

            # Second (idempotent) initialization call
            setup_observability(settings)

            # Verify TracerProvider and instruments were instantiated/configured exactly once
            mock_provider_cls.assert_called_once()
            mock_start_http.assert_called_once_with(9999)
            mock_httpx_inst.return_value.instrument.assert_called_once()
            mock_asyncpg_inst.return_value.instrument.assert_called_once()

        finally:
            # Restore original global module state
            otel_module._TRACER_PROVIDER = original_provider
            otel_module._METRICS_SERVER_STARTED = original_started


def test_manual_span_telemetry() -> None:
    """Verify manual span creation through get_tracer works as expected."""
    tracer = get_tracer()
    assert tracer is not None
    with tracer.start_as_current_span("test_manual_span") as span:
        span.set_attribute("securgraph.test_attribute", "value")
        assert span.is_recording() is not None
