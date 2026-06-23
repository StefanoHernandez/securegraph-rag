"""Back-compat shim — delegates to :mod:`securgraph.adapters.llm.ollama`.

The Ollama adapter was promoted to the provider-agnostic ``adapters/llm/``
package so SecurGraph can swap LLM providers (OpenAI, Google, Mistral,
Anthropic) without touching call sites (blueprint "Recommended
Technology Stack"). This module re-exports the previous public surface so
existing imports keep working while callers migrate to
:func:`securgraph.adapters.llm.get_llm_adapter`.

New code should use ``get_llm_adapter()`` / ``get_llm_adapter_for(role)``
instead of importing Ollama directly.
"""

from __future__ import annotations

from securgraph.adapters.llm.base import (
    AtomicClaim,
    ClaimDecomposition,
    NLIResult,
)
from securgraph.adapters.llm.ollama import (
    OllamaLLMAdapter,
    get_ollama_client,
)
from securgraph.domain.graph import ExtractedGraphSlice

# Re-export the structured outputs that callers used to import from here.
__all__ = [
    "AtomicClaim",
    "ClaimDecomposition",
    "NLIResult",
    "extract_claims",
    "extract_graph_slice",
    "get_ollama_adapter",
    "get_ollama_client",
    "judge_nli",
]


def get_ollama_adapter() -> OllamaLLMAdapter:
    """Return an OllamaLLMAdapter using the default settings."""
    from securgraph.config import get_settings

    return OllamaLLMAdapter(role="generation", settings=get_settings())


async def extract_graph_slice(text: str) -> ExtractedGraphSlice:
    """Back-compat wrapper around the default Ollama extract adapter."""
    return await get_ollama_adapter().extract_graph_slice(text)


async def extract_claims(text: str) -> ClaimDecomposition:
    """Back-compat wrapper around the default Ollama claims adapter."""
    return await get_ollama_adapter().extract_claims(text)


async def judge_nli(claim: str, reference: str) -> NLIResult:
    """Back-compat wrapper around the default Ollama NLI judge adapter."""
    return await get_ollama_adapter().judge_nli(claim, reference)
