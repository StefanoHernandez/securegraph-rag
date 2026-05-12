"""Ingestion pipeline: parse → chunk → metadata → policy-bind → persist."""

INGESTION_PIPELINE_VERSION = "1.0.0"
"""Bump on any change to parsing/chunking/metadata logic.

Recorded in ``IngestionTrace`` so re-ingestion under a new pipeline version is
distinguishable from a clean re-run under the same version.
"""
