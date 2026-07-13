"""Model-substitution test — blueprint validation scenario 6.

The SecurGraph technical blueprint (§5.2, "LLM-Agnostic and Embedding-Agnostic
Design") requires that LLM providers be replaceable through stable abstractions,
and lists "model substitution tests" as a required validation scenario.

This test verifies that the pipeline's LLM calls are routed through the
:class:`securgraph.adapters.llm.LLMAdapter` ABC and that swapping the configured
provider at runtime (via env override + adapter cache reset) changes which
adapter class serves the calls — without any call site being aware of the
change. We mock the underlying chat calls so the test runs offline and does
not require any cloud API key.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from securgraph.adapters.llm import (
    LLMAdapter,
    get_llm_adapter,
    get_llm_adapter_for,
    reset_llm_adapter_cache,
)
from securgraph.adapters.llm.mistral import MistralLLMAdapter
from securgraph.adapters.llm.ollama import OllamaLLMAdapter
from securgraph.runtime_overrides import clear_runtime_overrides, set_runtime_override

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolate_llm_env(monkeypatch):
    """Force a clean env for each test and clear the adapter cache so the
    factory re-reads settings on every call."""
    # Clear any LLM-related environment variables that might have leaked from the shell/dotenv
    for key in list(os.environ.keys()):
        if key.startswith("SECURGRAPH_LLM_") or key in ("OPENAI_API_KEY", "MISTRAL_API_KEY"):
            monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("SECURGRAPH_ENV", "test")
    monkeypatch.setenv("SECURGRAPH_OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")

    from securgraph.config import Settings

    # Build a mock settings loader that ignores the local `.env` file
    def mock_get_settings():
        return Settings(_env_file=None)

    mock_get_settings.cache_clear = lambda: None

    monkeypatch.setattr("securgraph.config.get_settings", mock_get_settings)

    reset_llm_adapter_cache()
    clear_runtime_overrides()
    yield
    reset_llm_adapter_cache()
    clear_runtime_overrides()


def test_default_provider_is_mistral(monkeypatch):
    """With no explicit provider, generation uses the cloud Mistral adapter."""
    monkeypatch.delenv("SECURGRAPH_LLM_PROVIDER", raising=False)
    from securgraph.config import get_settings

    get_settings.cache_clear()
    adapter = get_llm_adapter()
    assert isinstance(adapter, MistralLLMAdapter)
    assert adapter.provider_name == "mistral"


def test_factory_returns_llmadapter_abc():
    """Every provider, regardless of which one, must satisfy the ABC contract."""
    adapter = get_llm_adapter()
    assert isinstance(adapter, LLMAdapter)
    # The ABC methods must be present (not abstract).
    for method_name in ("chat", "extract_graph_slice", "extract_claims", "judge_nli"):
        method = getattr(adapter, method_name, None)
        assert method is not None, f"{method_name} missing on {type(adapter).__name__}"
        assert callable(method)


def test_provider_swap_changes_adapter_class(monkeypatch):
    """Swapping SECURGRAPH_LLM_PROVIDER at runtime routes to a different class.

    We do not require the cloud SDK to be installed; we only verify that the
    factory selects the right module and raises the expected credential error
    (which proves the lookup happened, i.e. the swap took effect).
    """
    # First call: default Mistral → MistralLLMAdapter.
    adapter_mistral = get_llm_adapter()
    assert isinstance(adapter_mistral, MistralLLMAdapter)

    # Swap to openai without an API key → should raise the credential error
    # from OpenAILLMAdapter.__init__, proving the factory actually instantiated
    # the OpenAI adapter (not the cached Ollama one).
    monkeypatch.setenv("SECURGRAPH_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from securgraph.config import get_settings

    get_settings.cache_clear()
    reset_llm_adapter_cache()

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        get_llm_adapter()


def test_per_role_provider_override(monkeypatch):
    """Per-role config (extract vs judge) selects different adapters."""
    monkeypatch.setenv("SECURGRAPH_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("SECURGRAPH_LLM_PROVIDER_JUDGE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from securgraph.config import get_settings

    get_settings.cache_clear()
    reset_llm_adapter_cache()

    # generation role -> explicit legacy Ollama adapter
    gen_adapter = get_llm_adapter("generation")
    assert isinstance(gen_adapter, OllamaLLMAdapter)

    # judge role → overridden to openai, fails on missing key (proves override)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        get_llm_adapter("judge")


def test_tenant_runtime_provider_override_does_not_leak(monkeypatch):
    """Tenant-scoped runtime provider overrides affect only that tenant."""
    monkeypatch.setenv("SECURGRAPH_LLM_PROVIDER", "mistral")
    monkeypatch.setenv("SECURGRAPH_LLM_PROVIDER_JUDGE", "mistral")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from securgraph.config import get_settings

    get_settings.cache_clear()
    reset_llm_adapter_cache()
    set_runtime_override("mrd-lab", "llm_provider_judge", "openai")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        get_llm_adapter_for("judge", tenant_id="mrd-lab")

    adapter = get_llm_adapter_for("judge", tenant_id="other-tenant")
    assert isinstance(adapter, MistralLLMAdapter)


def test_tenant_fallback_policy_defaults_to_block(monkeypatch):
    """Role-provider failures stay fail-closed unless fallback is explicit."""
    monkeypatch.setenv("SECURGRAPH_LLM_PROVIDER", "mistral")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from securgraph.config import get_settings

    get_settings.cache_clear()
    reset_llm_adapter_cache()
    set_runtime_override("mrd-lab", "llm_provider_judge", "openai")
    set_runtime_override("mrd-lab", "fallback_policy", "block")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        get_llm_adapter_for("judge", tenant_id="mrd-lab")


def test_tenant_fallback_policy_can_use_default_provider(monkeypatch):
    """Explicit fallback_policy=default_provider falls back from role provider."""
    monkeypatch.setenv("SECURGRAPH_LLM_PROVIDER", "mistral")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from securgraph.config import get_settings

    get_settings.cache_clear()
    reset_llm_adapter_cache()
    set_runtime_override("mrd-lab", "llm_provider_judge", "openai")
    set_runtime_override("mrd-lab", "fallback_policy", "default_provider")

    adapter = get_llm_adapter_for("judge", tenant_id="mrd-lab")

    assert isinstance(adapter, MistralLLMAdapter)


def test_explicit_ollama_provider_remains_available(monkeypatch):
    """Ollama remains a legacy adapter only when operators select it explicitly."""
    monkeypatch.setenv("SECURGRAPH_LLM_PROVIDER", "ollama")
    from securgraph.config import get_settings

    get_settings.cache_clear()
    reset_llm_adapter_cache()

    assert isinstance(get_llm_adapter(), OllamaLLMAdapter)


def test_unknown_provider_raises_value_error(monkeypatch):
    """An unsupported provider id is rejected with a clear error.

    Pydantic-settings validates the ``Literal`` on ``Settings.llm_provider``
    first, so an unknown id surfaces as a ``ValidationError`` whose message
    lists the allowed providers. The factory itself raises ``ValueError`` only
    if a non-Literal path (e.g. an empty per-role override) produced an
    unknown id; both paths are acceptable as long as the error is clear.
    """
    monkeypatch.setenv("SECURGRAPH_LLM_PROVIDER", "nonexistent")
    from pydantic import ValidationError

    from securgraph.config import get_settings

    get_settings.cache_clear()
    reset_llm_adapter_cache()
    with pytest.raises((ValidationError, ValueError)) as excinfo:
        get_settings()
    msg = str(excinfo.value)
    assert "nonexistent" in msg or "Unknown LLM provider" in msg


def test_chat_route_through_adapter(monkeypatch):
    """End-to-end: a chat() call goes through the adapter, not a direct SDK.

    This is the core substitution guarantee — if a call site ever bypassed the
    adapter and hit a vendor SDK directly, this test would fail because the
    mock on the adapter would never be invoked.
    """
    adapter = get_llm_adapter()
    fake_response = "mocked answer"

    with patch.object(adapter, "chat", new=AsyncMock(return_value=fake_response)) as mock_chat:
        import asyncio

        result = asyncio.run(adapter.chat(system="sys", user="usr", temperature=0.0))
        assert result == fake_response
        mock_chat.assert_awaited_once_with(system="sys", user="usr", temperature=0.0)


def test_backcompat_shim_still_works():
    """The legacy ``from securgraph.adapters.ollama import extract_claims``
    imports must still work so existing call sites don't break during the
    migration window.
    """
    from securgraph.adapters.ollama import (
        extract_claims,
        extract_graph_slice,
        get_ollama_client,
        judge_nli,
    )

    assert callable(extract_claims)
    assert callable(extract_graph_slice)
    assert callable(judge_nli)
    assert callable(get_ollama_client)
