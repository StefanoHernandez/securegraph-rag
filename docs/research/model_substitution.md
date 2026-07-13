---
title: Model Configuration and Judge Independence
status: current
---

# Model Configuration and Judge Independence

SecureGraph keeps LLM providers behind `LLMAdapter`, but the submission and
acceptance profile is deliberately fixed rather than provider-neutral:

| Stage | Provider | Model |
| --- | --- | --- |
| Generation | Mistral | `mistral-small-latest` |
| Graph extraction | Mistral | `mistral-small-latest` |
| NLI / semantic judge | OpenAI | `gpt-4o-mini` |
| Embeddings | local FlagEmbedding | `BAAI/bge-m3` |

This separation makes the NLI and semantic diagnostics independent from the
generation/extraction provider. It does not by itself prove statistical
independence; the evaluation reports preserve provider/model metadata so that
reviewers can assess the configuration.

## Provider Adapter Boundary

The pipeline uses `get_llm_adapter()` / `get_llm_adapter_for()` rather than
calling vendor SDKs directly. OpenAI, Mistral, Google, and Anthropic adapters
remain available. Ollama is retained only as an explicit legacy local adapter:
stage and production startup reject it, and it is excluded from cloud defaults,
acceptance tests, held-out evaluation, and paper experiments.

## Configuration

```dotenv
SECURGRAPH_LLM_PROVIDER=mistral
SECURGRAPH_LLM_MODEL=mistral-small-latest
SECURGRAPH_LLM_PROVIDER_EXTRACT=mistral
SECURGRAPH_LLM_MODEL_EXTRACT=mistral-small-latest
SECURGRAPH_LLM_PROVIDER_JUDGE=openai
SECURGRAPH_LLM_MODEL_JUDGE=gpt-4o-mini
MISTRAL_API_KEY=<configured-secret>
OPENAI_API_KEY=<configured-secret>
```

Cloud calls receive only evidence that passed the authorization layer. Local
BGE-M3 embeddings keep chunk embedding on the host. Provider/model coherence
guards reject local-shaped model identifiers for cloud providers and reject
Ollama in stage/prod.

## Verification

`tests/unit/test_llm_adapter_swap.py` verifies adapter selection and the
submission defaults. Held-out runs validate their provider/model manifest
before contacting the live endpoint; this prevents a legacy or mismatched
model configuration from producing a submission report.
