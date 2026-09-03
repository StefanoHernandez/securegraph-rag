"""The registry decides what leaves the trust boundary.

`expose_message` is the single switch between "this message was written for
the caller" and "this message may embed a DSN, a SQL statement, or key
material". Both directions are asserted here because getting it wrong in
either direction is how the leak this module exists to close was introduced.
"""

from __future__ import annotations

import pytest

from securgraph.api.errors import ERROR_MAP, detail_for, spec_for
from securgraph.corpus.manifest import CorpusManifestError
from securgraph.domain.query import QueryScopeError
from securgraph.ingestion.limits import UploadTooLargeError
from securgraph.ingestion.service import IngestionCompensationError, IngestionError
from securgraph.policy.egress import EgressBlockedError
from securgraph.security.secrets import SecretEncryptionError
from securgraph.services.provider_catalog import EgressUrlError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("exc", "code", "status"),
    [
        (EgressBlockedError("t", "openai", "judge", "blocked"), "egress_blocked", 451),
        (QueryScopeError("bad scope"), "query_scope_invalid", 422),
        (UploadTooLargeError(), "upload_too_large", 413),
        (IngestionError("boom"), "ingestion_failed", 500),
        (SecretEncryptionError("boom"), "secret_unavailable", 503),
        (CorpusManifestError("boom"), "corpus_manifest_invalid", 400),
        (EgressUrlError("boom"), "egress_url_invalid", 400),
    ],
)
def test_registered_exceptions_resolve_to_their_spec(exc, code, status):
    spec = spec_for(exc)
    assert spec is not None
    assert (spec.code, spec.status) == (code, status)


def test_subclass_resolves_through_the_mro():
    # IngestionCompensationError has no entry of its own; it must inherit
    # IngestionError's rather than fall through to the unhandled path.
    # Its constructor is (original: Exception, errors: tuple[str, ...]) —
    # see securgraph/ingestion/service.py:429.
    exc = IngestionCompensationError(RuntimeError("boom"), ("qdrant delete failed",))
    spec = spec_for(exc)
    assert spec is not None
    assert spec.code == "ingestion_failed"


def test_unregistered_exception_has_no_spec():
    assert spec_for(RuntimeError("something internal")) is None


def test_exposed_message_is_the_exception_text():
    exc = EgressBlockedError("mrd", "openai", "judge", "default egress policy")
    spec = spec_for(exc)
    assert spec is not None
    assert "Egress blocked by tenant policy" in detail_for(exc, spec)


@pytest.mark.parametrize(
    "exc",
    [
        IngestionError("connection to postgres://user:pw@host/db failed"),
        SecretEncryptionError("key id AKIA... rejected"),
        CorpusManifestError("/srv/securgraph/corpus/mrd_v1/manifest.yaml is invalid"),
    ],
)
def test_unexposed_messages_never_reach_detail(exc):
    spec = spec_for(exc)
    assert spec is not None
    assert detail_for(exc, spec) == spec.message
    assert str(exc) not in detail_for(exc, spec)


def test_every_exposed_spec_is_deliberate():
    # A new registry entry defaults to closed. This pins the exposed set so
    # widening it has to be an explicit edit to this list, seen in review.
    exposed = {spec.code for spec in ERROR_MAP.values() if spec.expose_message}
    assert exposed == {"egress_blocked", "query_scope_invalid", "egress_url_invalid"}
