---
title: Model Substitution & Judge Independence
updated: 2026-06-18
status: draft
sources:
  - SecurGraph_RAG_Technical_Blueprint.md (§5.2, validation scenario 6)
  - src/securgraph/adapters/llm/
  - tests/unit/test_llm_adapter_swap.py
---

# Model Substitution & Judge Independence

This document records how SecurGraph satisfies two related requirements from
the technical blueprint:

1. **Model flexibility KPI** (blueprint validation scenario 6):
   "Support for multiple LLM and embedding backends, including at least one
   local or self-hosted deployment option."
2. **Eval-judge independence** (work-tracker P1.4): the RAGAS evaluation judge
   must not be the same model as the pipeline's internal NLI judge, otherwise
   the evaluation is circular.

## 1. Model flexibility — LLM adapter layer

### Architecture

SecurGraph's pipeline never imports a vendor SDK directly. All LLM calls go
through the `LLMAdapter` ABC defined in
`src/securgraph/adapters/llm/base.py`. Concrete providers implement the ABC
under sibling modules:

| Provider   | Module                          | Default for |
|------------|---------------------------------|-------------|
| Ollama     | `adapters/llm/ollama.py`        | local/dev   |
| OpenAI     | `adapters/llm/openai.py`        | cloud       |
| Google     | `adapters/llm/google.py`        | cloud       |
| Mistral    | `adapters/llm/mistral.py`       | cloud       |
| Anthropic  | `adapters/llm/anthropic.py`     | cloud       |

The factory `get_llm_adapter(role)` reads `SECURGRAPH_LLM_PROVIDER` (and
per-role overrides `SECURGRAPH_LLM_PROVIDER_EXTRACT` / `_JUDGE`) from
settings and returns a cached adapter instance. Per-role overrides allow a
mixed deployment: e.g. local Ollama for extraction of sensitive documents
and a cloud model for the NLI judge over already-authorized evidence.

### Verification — blueprint scenario 6

`tests/unit/test_llm_adapter_swap.py` (7 tests, all passing) verifies:

1. The default provider is Ollama (sovereign-default guarantee — a fresh
   checkout with zero cloud credentials produces a working adapter).
2. Every provider satisfies the `LLMAdapter` ABC.
3. Swapping `SECURGRAPH_LLM_PROVIDER` at runtime routes to a different class.
4. Per-role overrides select different adapters.
5. An unsupported provider id is rejected with a clear error.
6. A `chat()` call goes through the adapter, not a direct vendor SDK.
7. The legacy `from securgraph.adapters.ollama import ...` shim still works.

### Configuration

Cloud providers are optional extras in `pyproject.toml`:

```
uv pip install -e ".[llm_openai,llm_google,llm_anthropic]"
```

Mistral reuses the OpenAI SDK with `OPENAI_BASE_URL=https://api.mistral.ai/v1`,
so it has no separate extra. The default Ollama provider needs no extra deps.

`.env` example for a mixed deployment:

```
SECURGRAPH_LLM_PROVIDER=ollama              # online path: local
SECURGRAPH_LLM_MODEL=granite4.1:3b
SECURGRAPH_LLM_PROVIDER_EXTRACT=ollama      # extraction of sensitive docs: local
SECURGRAPH_LLM_MODEL_EXTRACT=gemma4:e2b-it-qat
SECURGRAPH_LLM_PROVIDER_JUDGE=openai        # NLI judge over authorized evidence: cloud
SECURGRAPH_LLM_MODEL_JUDGE=gpt-4o-mini
OPENAI_API_KEY=sk-...
```

### Security note (blueprint §5.6)

Cloud providers receive only *already-authorized* evidence. The policy layer
runs before context assembly, so unauthorized chunks never enter the prompt
regardless of which LLM provider serves the call. The per-role config lets
operators keep extraction of raw sensitive documents on a local provider
while still using a cloud model for the judge over authorized context.

## 2. Eval-judge independence (P1.4)

### Problem

RAGAS evaluates the pipeline's answers against ground truth. If the RAGAS
judge LLM is the same model as the pipeline's internal NLI judge
(`workflow.py:judge_nli`), the evaluation is circular: the same model is
both producing and grading the answers. This is indefensible in peer review.

### Solution

The baseline harness (`eval/baseline.py`) uses a dedicated judge model that
is **different** from the pipeline's internal judge:

| Role                  | Config                          | Default model       |
|-----------------------|---------------------------------|---------------------|
| Pipeline NLI judge    | `SECURGRAPH_LLM_MODEL_JUDGE`    | `granite4.1:3b`     |
| RAGAS eval judge      | hardcoded in `baseline.py`      | `qwen3.5:2b`        |
| RAGAS eval embeddings | hardcoded in `baseline.py`      | `nomic-embed-text`  |

The RAGAS judge also runs with `think=False` (via the `OllamaThinkOff` wrapper
in `baseline.py`) to keep latency bounded on long prompts.

### Verification

This is documented here rather than tested in code, because the independence
is a property of the *configuration*, not of the code. The test that the
pipeline uses `get_llm_adapter_for("judge")` (which reads
`SECURGRAPH_LLM_PROVIDER_JUDGE` / `SECURGRAPH_LLM_MODEL_JUDGE`) is covered by
`test_llm_adapter_swap.py::test_per_role_provider_override`.

## 3. Open follow-ups

- **Live smoke test per provider**: `tests/integration/test_llm_provider_live.py`
  that issues one real chat call per configured provider (requires API keys).
  Not blocking for the paper; the unit-level substitution test is sufficient
  for the KPI claim.
- **Antigravity as orchestration alternative**: `google-antigravity` is an
  agentic framework comparable to LangGraph, not an LLM provider. Tracked as
  a separate backlog item (`feat/orchestration-antigravity`).
- **Pin `uv.lock`** on the branch that produces paper figures, so the
  provider SDK versions are reproducible (work-tracker INFRA-2).
