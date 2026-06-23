"""LLM adapter package — provider-agnostic access to language models.

Re-exports the public surface from :mod:`securgraph.adapters.llm.base` plus a
back-compat shim for callers that used to import the structured outputs and
helpers directly from :mod:`securgraph.adapters.ollama`.
"""

from __future__ import annotations

from securgraph.adapters.llm.base import (
    CLAIM_DECOMPOSITION_SYSTEM_PROMPT,
    EXTRACT_SYSTEM_PROMPT,
    NLI_SYSTEM_PROMPT_V1,
    NLI_SYSTEM_PROMPT_V2,
    NLI_SYSTEM_PROMPT_V3,
    AtomicClaim,
    ClaimDecomposition,
    LLMAdapter,
    LLMRole,
    NLIResult,
    get_llm_adapter,
    get_llm_adapter_for,
    reset_llm_adapter_cache,
    robust_json_loads,
)

__all__ = [
    "CLAIM_DECOMPOSITION_SYSTEM_PROMPT",
    "EXTRACT_SYSTEM_PROMPT",
    "NLI_SYSTEM_PROMPT_V1",
    "NLI_SYSTEM_PROMPT_V2",
    "NLI_SYSTEM_PROMPT_V3",
    "AtomicClaim",
    "ClaimDecomposition",
    "LLMAdapter",
    "LLMRole",
    "NLIResult",
    "get_llm_adapter",
    "get_llm_adapter_for",
    "reset_llm_adapter_cache",
    "robust_json_loads",
]
