"""LLM adapter abstraction layer.

SecurGraph is LLM-agnostic by design (blueprint
"Recommended Technology Stack"). All application logic depends on the
``LLMAdapter`` ABC defined here; concrete providers (Ollama, OpenAI,
Google Gemini, Mistral, Anthropic) implement it under sibling modules.

Public surface:
    - ``LLMAdapter``            — async ABC every provider implements.
    - ``AtomicClaim`` / ``ClaimDecomposition`` / ``NLIResult`` — shared
      structured outputs returned by ``extract_claims`` and ``judge_nli``.
    - ``get_llm_adapter()``     — cached factory reading
      ``SECURGRAPH_LLM_PROVIDER`` from settings.
    - ``get_llm_adapter_for(role)`` — per-role factory so a deployment
      can mix providers (e.g. local Ollama for extraction of sensitive
      data, cloud OpenAI for the NLI judge over already-authorized
      evidence), as prescribed by blueprint §5.2.
    - Shared system prompts (``EXTRACT_SYSTEM_PROMPT``,
      ``CLAIM_DECOMPOSITION_SYSTEM_PROMPT``, ``NLI_SYSTEM_PROMPT_V1/V2/V3``)
      reused verbatim by every adapter so prompt-engineering stays in one
      place and cross-provider comparisons are meaningful.

Roles
-----
The factory accepts a ``role`` discriminator so config can target each
pipeline stage independently:

    role="extract"     -> settings.llm_provider_extract / llm_model_extract
    role="judge"       -> settings.llm_provider_judge  / llm_model_judge
    role="generation"  -> settings.llm_provider / llm_model  (default)

If a per-role provider is unset it falls back to the default
``SECURGRAPH_LLM_PROVIDER``. This keeps the simple case simple
(one provider for everything) while allowing the policy-mixed
deployment the blueprint describes.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from securgraph.config import Settings, get_settings
from securgraph.domain.graph import ExtractedGraphSlice
from securgraph.logging import get_logger

_LOG = get_logger(__name__)


# ---------------------------------------------------------------------------
# Shared structured outputs (formerly in adapters/ollama.py)
# ---------------------------------------------------------------------------


class AtomicClaim(BaseModel):
    claim_id: str = Field(description="Unique ID, e.g. 'c0', 'c1'")
    text: str = Field(description="Self-contained, atomic, factual claim.")
    entities: list[str] = Field(
        default_factory=list,
        description="Key entities mentioned in the claim.",
    )


class ClaimDecomposition(BaseModel):
    claims: list[AtomicClaim]


class NLIResult(BaseModel):
    label: Literal["entailment", "neutral", "contradiction"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(description="Brief explanation.")


# ---------------------------------------------------------------------------
# Shared system prompts — every adapter uses these verbatim so that
# cross-provider comparisons (KPI: model flexibility, blueprint scenario 6)
# are comparing model behaviour, not prompt drift.
# ---------------------------------------------------------------------------

EXTRACT_SYSTEM_PROMPT = (
    "You are an expert knowledge graph extraction agent. Analyze the provided text chunk and "
    "extract all entities and their semantic relationships.\n"
    "Output strictly in JSON matching this schema:\n"
    "{\n"
    '  "entities": [{"name": "entity_name", "label": "ENTITY_TYPE"}],\n'
    '  "relations": [{"source_entity_name": "name", "target_entity_name": "name", '
    '"relationship_type": "TYPE", "confidence": 0.9}]\n'
    "}\n"
    "If no entities/relations are found, return empty lists. Do not write "
    "markdown blocks or text, return ONLY the raw JSON."
)

CLAIM_DECOMPOSITION_SYSTEM_PROMPT = (
    "You are a precise claim decomposition assistant. Decompose the user's text into "
    "independent, atomic, self-contained factual claims and extract key entities.\n"
    "Output strictly in JSON matching this schema:\n"
    "{\n"
    '  "claims": [\n'
    '    {\n'
    '      "claim_id": "c0",\n'
    '      "text": "The factual claim statement.",\n'
    '      "entities": ["entity1", "entity2"]\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "If no claims are found, return an empty list under 'claims'. Do not write markdown "
    "blocks or text, return ONLY the raw JSON."
)

NLI_SYSTEM_PROMPT_V1 = (
    "You are an expert NLI judge classifying relationships. Analyze whether the Claim is "
    "supported by the Reference Context.\n"
    "Classify the relationship as exactly one of: entailment, neutral, or contradiction.\n"
    "Output strictly in JSON matching this schema:\n"
    "{\n"
    '  "label": "entailment",\n'
    '  "confidence": 0.95,\n'
    '  "reason": "Explain why this label was chosen."\n'
    "}\n"
    "Do not write markdown blocks or text, return ONLY the raw JSON."
)

NLI_SYSTEM_PROMPT_V2 = (
    "You are a highly conservative and strict NLI judge. Analyze whether the Claim is fully "
    "and explicitly supported by the Reference Context.\n"
    "If there are any assumptions, logical jumps, or missing details, classify as neutral.\n"
    "Classify exactly as: entailment, neutral, or contradiction.\n"
    "Output strictly in JSON matching this schema:\n"
    "{\n"
    '  "label": "neutral",\n'
    '  "confidence": 0.90,\n'
    '  "reason": "Reason for choice."\n'
    "}\n"
    "Do not write markdown blocks or text, return ONLY the raw JSON."
)

NLI_SYSTEM_PROMPT_V3 = (
    "You are an NLI judge focused on detecting factual support or contradiction.\n"
    "Check if the Claim directly contradicts the Context (contradiction) or is fully "
    "validated by it (entailment), else classify as neutral.\n"
    "Classify exactly as: entailment, neutral, or contradiction.\n"
    "Output strictly in JSON matching this schema:\n"
    "{\n"
    '  "label": "contradiction",\n'
    '  "confidence": 0.95,\n'
    '  "reason": "Explain choice."\n'
    "}\n"
    "Do not write markdown blocks or text, return ONLY the raw JSON."
)


# ---------------------------------------------------------------------------
# Robust JSON parsing — shared utility (formerly _robust_json_loads in
# adapters/ollama.py). Small LLMs and some cloud providers occasionally wrap
# JSON in markdown fences or leave trailing commas; this keeps the pipeline
# alive without throwing.
# ---------------------------------------------------------------------------


def robust_json_loads(content: str) -> Any:
    """Parse JSON robustly: strip markdown fences and trailing commas."""
    content_stripped = content.strip()
    if not content_stripped:
        return {}
    if content_stripped.startswith("```"):
        lines = content_stripped.splitlines()
        if lines[0].startswith("```json") or lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content_stripped = "\n".join(lines).strip()
    content_stripped = re.sub(r",(\s*[}\]])", r"\1", content_stripped)
    return json.loads(content_stripped)


# ---------------------------------------------------------------------------
# Adapter ABC
# ---------------------------------------------------------------------------

LLMRole = Literal["extract", "judge", "generation"]


class LLMAdapter(ABC):
    """Async LLM provider abstraction.

    Every method maps to a pipeline stage that previously called Ollama
    directly. Adapters must be safe to cache and reuse across calls; they
    hold no per-request state.
    """

    provider_name: str = "abstract"

    @abstractmethod
    async def chat(
        self,
        *,
        system: str,
        user: str,
        json_mode: bool = False,
        temperature: float = 0.0,
        think: bool = False,
        keep_alive: int | None = None,
    ) -> str:
        """Low-level chat completion. Returns the assistant message text.

        ``json_mode`` requests structured-JSON output where the provider
        supports it. ``think`` is forwarded to providers that expose a
        reasoning toggle (Ollama thinking models, OpenAI reasoning_effort);
        other providers ignore it. ``keep_alive`` is a hint in seconds for
        providers that manage model lifecycle (Ollama); ignored elsewhere.
        """

    @abstractmethod
    async def extract_graph_slice(self, text: str) -> ExtractedGraphSlice:
        """Extract entities and relations from a chunk (ingestion stage)."""

    @abstractmethod
    async def extract_claims(self, text: str) -> ClaimDecomposition:
        """Decompose a draft into atomic claims (consensus stage)."""

    @abstractmethod
    async def judge_nli(self, claim: str, reference: str) -> NLIResult:
        """NLI classification of a claim against a reference (verifier)."""


# ---------------------------------------------------------------------------
# Provider registry + factory
# ---------------------------------------------------------------------------

# Provider id -> module path implementing LLMAdapter. Populated lazily so
# missing optional deps (e.g. openai, anthropic) don't crash imports when
# the user runs the default Ollama provider.
_PROVIDER_MODULES: dict[str, str] = {
    "ollama": "securgraph.adapters.llm.ollama",
    "openai": "securgraph.adapters.llm.openai",
    "google": "securgraph.adapters.llm.google",
    "mistral": "securgraph.adapters.llm.mistral",
    "anthropic": "securgraph.adapters.llm.anthropic",
}


def _resolve_provider_id(role: LLMRole, settings: Settings) -> str:
    """Pick the provider id for a given role, falling back to the default."""
    if role == "extract":
        pid = settings.llm_provider_extract or settings.llm_provider
    elif role == "judge":
        pid = settings.llm_provider_judge or settings.llm_provider
    else:
        pid = settings.llm_provider
    if pid not in _PROVIDER_MODULES:
        raise ValueError(
            f"Unknown LLM provider {pid!r}. Supported: {sorted(_PROVIDER_MODULES)}"
        )
    return pid


def _instantiate(provider_id: str, role: LLMRole, settings: Settings) -> LLMAdapter:
    """Import the provider module lazily and call its ``build_adapter``."""
    import importlib

    module = importlib.import_module(_PROVIDER_MODULES[provider_id])
    builder = module.build_adapter
    return cast(LLMAdapter, builder(role=role, settings=settings))


# Cached per (provider_id, role) so repeated calls reuse the same instance.
# The cache key includes both because a deployment may mix providers per
# role (e.g. ollama for extract, openai for judge).
@lru_cache(maxsize=8)
def _cached_adapter(provider_id: str, role: LLMRole) -> LLMAdapter:
    # Re-read settings at build time so test config overrides take effect.
    return _instantiate(provider_id, role, get_settings())


def get_llm_adapter(role: LLMRole = "generation") -> LLMAdapter:
    """Return the cached LLMAdapter for the given role.

    Resolution order for the provider id:
        1. role-specific setting (``llm_provider_extract`` / ``_judge``)
        2. default ``llm_provider``
    """
    settings = get_settings()
    pid = _resolve_provider_id(role, settings)
    return _cached_adapter(pid, role)


def get_llm_adapter_for(role: LLMRole) -> LLMAdapter:
    """Alias of :func:`get_llm_adapter` for readability at call sites."""
    return get_llm_adapter(role)


def reset_llm_adapter_cache() -> None:
    """Clear the adapter cache. Used by tests after overriding env config."""
    _cached_adapter.cache_clear()


__all__ = [
    "CLAIM_DECOMPOSITION_SYSTEM_PROMPT",
    "EXTRACT_SYSTEM_PROMPT",
    "NLI_SYSTEM_PROMPT_V1",
    "NLI_SYSTEM_PROMPT_V2",
    "NLI_SYSTEM_PROMPT_V3",
    "AtomicClaim",
    "LLMAdapter",
    "LLMRole",
    "NLIResult",
    "get_llm_adapter",
    "get_llm_adapter_for",
    "reset_llm_adapter_cache",
    "robust_json_loads",
]
