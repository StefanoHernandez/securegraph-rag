"""Base types shared across domain models.

Domain models are pure Pydantic objects: no I/O, no side effects, no framework
coupling. They are the lingua franca that every other package depends on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

# ----------------------------------------------------------------------------
# Type aliases
# ----------------------------------------------------------------------------

TenantId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_\-]+$")]
"""Stable, URL-safe tenant identifier (e.g. `"mrd"`)."""

UserId = Annotated[str, Field(min_length=1, max_length=256)]
"""OpenFGA subject id, expected in the form `"user:<slug>"` after normalization."""

TraceId = Annotated[str, Field(min_length=1, max_length=128)]
"""Correlation id propagated through every component for a single request."""


# ----------------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------------


class Classification(StrEnum):
    """Document / chunk / node / edge confidentiality level.

    Ordered from least to most restrictive. The string values are used as-is
    in OpenFGA tuples and in Qdrant payload pre-filters.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"

    @property
    def rank(self) -> int:
        return {Classification.PUBLIC: 0, Classification.INTERNAL: 1, Classification.RESTRICTED: 2}[
            self
        ]


class DocumentType(StrEnum):
    POLICY = "policy"
    CONTRACT = "contract"
    REPORT = "report"
    SPECIFICATION = "specification"
    NOTE = "note"
    OTHER = "other"


class IngestionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ALREADY_INGESTED = "already_ingested"


# ----------------------------------------------------------------------------
# Base model
# ----------------------------------------------------------------------------


class SGModel(BaseModel):
    """Project-wide Pydantic base.

    - `extra="forbid"`: catch typos at parse time, important for stable API contracts.
    - `frozen=True`: domain objects are values. State changes happen by constructing
      new instances; this prevents accidental mutation across async boundaries.
    - `populate_by_name=True`: allow both alias and field name on input.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class Subject(SGModel):
    """A request principal — the identity making a query.

    Identity carriers, not authority sources. Authority always comes from OpenFGA;
    this object only labels who is asking.
    """

    user_id: UserId
    tenant_id: TenantId
    roles: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def fga_user(self) -> str:
        """Canonical OpenFGA user reference, e.g. ``"user:alice"``."""
        return self.user_id if ":" in self.user_id else f"user:{self.user_id}"


class Scope(SGModel):
    """Optional query-time scope hints.

    Hints, not gates: actual authorization remains in OpenFGA. These narrow
    candidate sets early for performance and explainability.
    """

    collections: tuple[str, ...] = Field(default_factory=tuple)
    max_classification: Classification | None = None
    document_ids: tuple[str, ...] = Field(default_factory=tuple)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def utcnow() -> datetime:
    """Timezone-aware UTC now. Always use this — never ``datetime.utcnow()``."""
    return datetime.now(UTC)


def dump_jsonable(model: BaseModel) -> dict[str, Any]:
    """Serialize a model to a JSON-safe dict (datetimes → isoformat, etc.)."""
    return model.model_dump(mode="json")
