"""Upload budget for ingestion.

The limit is an ingestion rule, not a transport rule: the worker path enforces
the same budget without going through an HTTP route. Kept next to the pipeline
so both callers read one definition.
"""

from __future__ import annotations

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024


class UploadTooLargeError(Exception):
    """Raised as soon as an upload passes the ingestion budget."""
