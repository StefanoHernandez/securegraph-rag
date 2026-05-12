"""OpenTelemetry integration."""

from securgraph.observability.otel import setup_observability, shutdown_observability

__all__ = ["setup_observability", "shutdown_observability"]
