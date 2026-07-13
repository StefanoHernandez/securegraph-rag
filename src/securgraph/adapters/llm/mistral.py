"""Mistral LLM adapter — OpenAI-compatible endpoint specialization.

Mistral's public API (``https://api.mistral.ai/v1``) implements the OpenAI
Chat Completions contract, so this adapter is a thin layer that reuses
:class:`securgraph.adapters.llm.openai.OpenAILLMAdapter` with the Mistral
base URL and a Mistral-specific API key.

The Mistral key is read from ``MISTRAL_API_KEY`` (separate from
``OPENAI_API_KEY`` so a deployment can use both providers concurrently under
different roles). If ``MISTRAL_API_KEY`` is unset, we fall back to
``OPENAI_API_KEY`` so operators who run a single OpenAI-compatible endpoint
(LiteLLM proxy, vLLM, etc.) only need to set one key.
"""

from __future__ import annotations

from securgraph.adapters.llm.base import LLMAdapter, LLMRole
from securgraph.adapters.llm.openai import OpenAILLMAdapter
from securgraph.config import Settings
from securgraph.logging import get_logger

_LOG = get_logger(__name__)

_MISTRAL_BASE_URL = "https://api.mistral.ai/v1"


class MistralLLMAdapter(OpenAILLMAdapter):
    """Mistral specialization of the OpenAI-compatible adapter."""

    provider_name = "mistral"

    def __init__(self, role: LLMRole, settings: Settings) -> None:
        # Inject the Mistral base URL + key into a settings view, then defer
        # to the OpenAI adapter constructor. We mutate a local copy so the
        # global settings object stays untouched for other roles.
        mistral_key = _resolve_mistral_key(settings)
        if not mistral_key:
            raise RuntimeError(
                "Mistral adapter selected but neither MISTRAL_API_KEY nor "
                "OPENAI_API_KEY is set. Set one of them or switch "
                "SECURGRAPH_LLM_PROVIDER to an installed cloud provider."
            )
        # Build a shallow settings clone with the Mistral endpoint + key.
        # Pydantic settings models are immutable by default; use model_copy.
        mistral_settings = settings.model_copy(
            update={
                "openai_base_url": _MISTRAL_BASE_URL,
                "openai_api_key": type(settings.openai_api_key)(mistral_key),
            }
        )
        super().__init__(role=role, settings=mistral_settings)
        # Override the provider_name set by the parent.
        self.provider_name = "mistral"


def _resolve_mistral_key(settings: Settings) -> str:
    """Prefer mistral_api_key from settings; fall back to openai_api_key."""
    key = settings.mistral_api_key.get_secret_value()
    if key:
        return key
    return settings.openai_api_key.get_secret_value()


def build_adapter(*, role: LLMRole, settings: Settings) -> LLMAdapter:
    return MistralLLMAdapter(role=role, settings=settings)


__all__ = ["MistralLLMAdapter", "build_adapter"]
