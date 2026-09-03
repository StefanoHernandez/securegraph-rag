"""Registry mapping domain exceptions to their client-facing form.

A registry rather than a common `AppError` base class: six sites catch
`ValueError`/`RuntimeError` generically and rely on the current bases
(`api/routes/settings.py:198`, `:212`, `api/routes/admin.py:205`,
`policy/egress.py:169`, `indexing/tamper.py:165`,
`adapters/llm/base.py:296`). Reparenting would silently change which handler
wins at each of them.

`expose_message` decides whether `str(exc)` may leave the process. It is
False by default. Setting it True asserts that the message is composed
entirely of values the caller supplied or is entitled to see — see the table
in docs/superpowers/specs/2026-09-03-p2-error-surface-design.md §3.2.1.
"""

from __future__ import annotations

from dataclasses import dataclass

from securgraph.corpus.manifest import CorpusManifestError
from securgraph.domain.query import QueryScopeError
from securgraph.ingestion.limits import MAX_UPLOAD_BYTES, UploadTooLargeError
from securgraph.ingestion.service import IngestionError
from securgraph.policy.egress import EgressBlockedError
from securgraph.security.secrets import SecretEncryptionError
from securgraph.services.provider_catalog import EgressUrlError


@dataclass(frozen=True)
class ErrorSpec:
    """How one exception type is presented to the caller."""

    code: str
    status: int
    message: str
    expose_message: bool = False


ERROR_MAP: dict[type[Exception], ErrorSpec] = {
    EgressBlockedError: ErrorSpec(
        code="egress_blocked",
        status=451,
        message="This request was blocked by the tenant egress policy.",
        # provider, stage and a fixed reason string: a policy outcome stated
        # to the tenant that configured the policy.
        expose_message=True,
    ),
    QueryScopeError: ErrorSpec(
        code="query_scope_invalid",
        status=422,
        message="The query scope is not valid for this request.",
        # Validation of the caller's own request scope.
        expose_message=True,
    ),
    EgressUrlError: ErrorSpec(
        code="egress_url_invalid",
        status=400,
        message="The provider base URL is not valid.",
        # Validation of a URL the tenant admin just submitted.
        expose_message=True,
    ),
    UploadTooLargeError: ErrorSpec(
        code="upload_too_large",
        status=413,
        message=(
            f"Document size exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB "
            "ingestion budget limit."
        ),
    ),
    CorpusManifestError: ErrorSpec(
        code="corpus_manifest_invalid",
        status=400,
        message="The corpus manifest is not valid.",
        # May embed server-side file paths.
    ),
    IngestionError: ErrorSpec(
        code="ingestion_failed",
        status=500,
        message="Ingestion failed. The failure has been recorded.",
        # Wraps cross-store write failures; can contain connection detail.
    ),
    SecretEncryptionError: ErrorSpec(
        code="secret_unavailable",
        status=503,
        message="A stored credential could not be read.",
        # Crypto and key-management internals.
    ),
}


def spec_for(exc: BaseException) -> ErrorSpec | None:
    """Return the spec registered for ``exc``'s type, walking its MRO.

    Walking the MRO means a subclass inherits its parent's presentation
    without needing its own entry — ``IngestionCompensationError`` resolves to
    ``IngestionError``. Returns None when nothing is registered, which is the
    signal to fall through to the unhandled path.
    """
    for klass in type(exc).__mro__:
        spec = ERROR_MAP.get(klass)
        if spec is not None:
            return spec
    return None


def detail_for(exc: BaseException, spec: ErrorSpec) -> str:
    """The string that goes into the response body. Never unconditionally str(exc)."""
    if spec.expose_message:
        text = str(exc)
        if text:
            return text
    return spec.message
