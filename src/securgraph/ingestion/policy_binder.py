"""Policy binding: apply document-level tags to all chunks.

M1 keeps this simple: chunks inherit their parent document's classification and
policy tags unless explicitly overridden. M4 wires the binder into OpenFGA
tuple emission; here we only attach metadata.
"""

from __future__ import annotations

from typing import Final

from securgraph.domain import Chunk, Classification

POLICY_BINDER_VERSION: Final = "1.0.0"


def bind_chunks(
    chunks: list[Chunk],
    *,
    classification: Classification,
    policy_tags: tuple[str, ...],
) -> list[Chunk]:
    """Return a new list of chunks with document-level policy fields applied.

    Idempotent and pure. Existing per-chunk overrides are NOT clobbered: a chunk
    whose ``classification`` already differs from the document default is left
    alone — but in M1 no override path exists yet, so this is currently a
    blanket apply.
    """
    bound: list[Chunk] = []
    for c in chunks:
        # In M1 the chunker emits chunks with the default classification; a
        # later milestone may pre-set per-chunk overrides at parse time.
        bound.append(
            c.model_copy(
                update={
                    "classification": classification,
                    "policy_tags": policy_tags,
                }
            )
        )
    return bound
