"""OpenAI Chat Completions LLM adapter.

Implements :class:`securgraph.adapters.llm.base.LLMAdapter` against the
OpenAI Chat Completions API. The same adapter also serves any
OpenAI-compatible endpoint (vLLM, LiteLLM, Mistral, Together, Groq, etc.)
via ``settings.openai_base_url`` — see :mod:`securgraph.adapters.llm.mistral`
for the thin Mistral specialization.

Security note (blueprint §5.6 "Privacy Rules"): cloud providers receive only
*already-authorized* evidence. Extraction of raw sensitive documents should
stay on a local provider (Ollama); this adapter is intended for the online
query path and the NLI judge over authorized context, or for deployments
where the corpus is non-sensitive. The per-role config
(``SECURGRAPH_LLM_PROVIDER_EXTRACT`` vs ``_JUDGE``) lets operators enforce
that split without code changes.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from securgraph.adapters.llm.base import (
    CLAIM_DECOMPOSITION_SYSTEM_PROMPT,
    EXTRACT_SYSTEM_PROMPT,
    LLMAdapter,
    LLMRole,
    NLI_SYSTEM_PROMPT_V1,
    NLI_SYSTEM_PROMPT_V2,
    NLI_SYSTEM_PROMPT_V3,
    NLIResult,
    AtomicClaim,
    ClaimDecomposition,
    robust_json_loads,
)
from securgraph.config import Settings, get_settings
from securgraph.domain.graph import ExtractedGraphSlice
from securgraph.logging import get_logger

_LOG = get_logger(__name__)


# ---------------------------------------------------------------------------
# Client cache. One AsyncOpenAI per (base_url, api_key) so swapping providers
# at runtime in tests doesn't leak connections.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _get_openai_client(base_url: str, api_key: str) -> Any:
    """Cached AsyncOpenAI client. Import is lazy so the ``openai`` package
    remains an optional dependency."""
    from openai import AsyncOpenAI

    return AsyncOpenAI(base_url=base_url, api_key=api_key)


def _normalize_extracted_slice(data: dict[str, Any]) -> ExtractedGraphSlice:
    """Validate entities/relations one-by-one. Same logic as the Ollama
    adapter, duplicated here to avoid a cross-adapter import; both behaviors
    must evolve together but keeping the validator local makes each adapter
    self-contained."""
    from securgraph.domain.graph import ExtractedEntity, ExtractedRelation

    entities: list[ExtractedEntity] = []
    for item in data.get("entities", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            entities.append(ExtractedEntity.model_validate(item))
        except Exception as e:
            _LOG.warning("dropping_malformed_entity", error=str(e), item=str(item)[:120])

    relations: list[ExtractedRelation] = []
    for item in data.get("relations", []) or []:
        if not isinstance(item, dict):
            continue
        if "confidence" not in item or item.get("confidence") is None:
            item["confidence"] = 0.5
        try:
            relations.append(ExtractedRelation.model_validate(item))
        except Exception as e:
            _LOG.warning("dropping_malformed_relation", error=str(e), item=str(item)[:120])

    return ExtractedGraphSlice(entities=entities, relations=relations)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class OpenAILLMAdapter(LLMAdapter):
    """OpenAI Chat Completions implementation of the LLMAdapter ABC.

    Also used for Mistral and other OpenAI-compatible endpoints by pointing
    ``settings.openai_base_url`` at the alternative API.
    """

    provider_name = "openai"

    def __init__(self, role: LLMRole, settings: Settings) -> None:
        self._role = role
        self._settings = settings
        if role == "extract":
            self._model = settings.llm_model_extract
        elif role == "judge":
            self._model = settings.llm_model_judge
        else:
            self._model = settings.llm_model
        api_key = settings.openai_api_key.get_secret_value()
        if not api_key:
            raise RuntimeError(
                "OpenAI adapter selected but OPENAI_API_KEY is not set. "
                "Either set the key or switch SECURGRAPH_LLM_PROVIDER back to "
                "'ollama'."
            )
        self._client = _get_openai_client(settings.openai_base_url, api_key)

    # ----- low-level chat ---------------------------------------------------

    async def chat(
        self,
        *,
        system: str,
        user: str,
        json_mode: bool = False,
        temperature: float = 0.0,
        think: bool | None = None,
        keep_alive: int | None = None,
    ) -> str:
        # ``keep_alive`` is an Ollama-only concept; ignored on cloud providers.
        # ``think`` is mapped to reasoning_effort on models that expose it; we
        # don't pass it by default to avoid changing behaviour on models that
        # don't support it.
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            response_format={"type": "json_object"} if json_mode else None,
        )
        return response.choices[0].message.content or ""

    # ----- extract_graph_slice ----------------------------------------------

    async def extract_graph_slice(self, text: str) -> ExtractedGraphSlice:
        try:
            content = await self.chat(
                system=EXTRACT_SYSTEM_PROMPT,
                user=text,
                json_mode=True,
                temperature=0.0,
            )
            data = robust_json_loads(content)
            return _normalize_extracted_slice(data)
        except Exception as e:
            _LOG.warning("openai_extraction_failed", error=str(e), text_sample=text[:100])
            return ExtractedGraphSlice(entities=[], relations=[])

    # ----- extract_claims ---------------------------------------------------

    async def extract_claims(self, text: str) -> ClaimDecomposition:
        try:
            content = await self.chat(
                system=CLAIM_DECOMPOSITION_SYSTEM_PROMPT,
                user=text,
                json_mode=True,
                temperature=0.0,
            )
            data = robust_json_loads(content)
        except Exception as e:
            _LOG.warning("openai_extract_claims_failed", error=str(e), text_sample=text[:100])
            return ClaimDecomposition(claims=[])

        normalized: list[AtomicClaim] = []
        if isinstance(data, dict) and "claims" in data and isinstance(data["claims"], list):
            for idx, item in enumerate(data["claims"]):
                if not isinstance(item, dict):
                    continue
                cid = item.get("claim_id") or item.get("id") or f"c{idx}"
                ctext = (
                    item.get("text")
                    or item.get("claim")
                    or item.get("content")
                    or ""
                )
                raw_entities = item.get("entities") or []
                entities: list[str] = []
                if isinstance(raw_entities, list):
                    for ent in raw_entities:
                        if isinstance(ent, str):
                            entities.append(ent)
                        elif isinstance(ent, dict) and "entity" in ent:
                            entities.append(ent["entity"])
                        elif isinstance(ent, dict) and "name" in ent:
                            entities.append(ent["name"])
                if ctext:
                    normalized.append(
                        AtomicClaim(
                            claim_id=str(cid),
                            text=str(ctext),
                            entities=entities,
                        )
                    )
        return ClaimDecomposition(claims=normalized)

    # ----- judge_nli --------------------------------------------------------

    async def _single_nli_vote(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
    ) -> NLIResult:
        try:
            content = await self.chat(
                system=system_prompt,
                user=user_prompt,
                json_mode=True,
                temperature=temperature,
            )
            data = robust_json_loads(content)

            label = data.get("label")
            if not label:
                for k in ("entailment", "neutral", "contradiction"):
                    if k in data and str(data[k]).lower() in ("true", "1", "yes"):
                        label = k
                        break
            if label not in ("entailment", "neutral", "contradiction"):
                label = "neutral"

            confidence = data.get("confidence")
            if confidence is None:
                confidence = 1.0 if label == "entailment" else 0.5
            try:
                confidence = float(confidence)
            except (ValueError, TypeError):
                confidence = 0.5

            reason = data.get("reason") or "Vote completed."
            return NLIResult(label=label, confidence=confidence, reason=reason)
        except Exception as e:
            _LOG.warning("openai_nli_vote_failed", error=str(e), temperature=temperature)
            return NLIResult(label="neutral", confidence=0.0, reason=f"Failed: {str(e)}")

    async def judge_nli(self, claim: str, reference: str) -> NLIResult:
        prompt = f"Claim: {claim}\nReference Context: {reference}"

        if not self._settings.feature_verifier_ensemble:
            return await self._single_nli_vote(NLI_SYSTEM_PROMPT_V1, prompt, 0.0)

        import asyncio

        votes = await asyncio.gather(
            self._single_nli_vote(NLI_SYSTEM_PROMPT_V1, prompt, 0.0),
            self._single_nli_vote(NLI_SYSTEM_PROMPT_V2, prompt, 0.3),
            self._single_nli_vote(NLI_SYSTEM_PROMPT_V3, prompt, 0.7),
        )

        counts = {"entailment": 0, "neutral": 0, "contradiction": 0}
        for v in votes:
            counts[v.label] += 1

        winning_label = "neutral"
        max_count = -1
        for label, count in counts.items():
            if count > max_count:
                max_count = count
                winning_label = label
            elif count == max_count:
                if label == "neutral" or (
                    label == "contradiction" and winning_label == "entailment"
                ):
                    winning_label = label

        winning_confidences = [v.confidence for v in votes if v.label == winning_label]
        if not winning_confidences:
            winning_confidences = [v.confidence for v in votes]
        avg_confidence = sum(winning_confidences) / len(winning_confidences)
        winning_reasons = "; ".join(
            [f"Voter: {v.reason}" for v in votes if v.label == winning_label]
        )

        return NLIResult(
            label=winning_label,
            confidence=round(avg_confidence, 4),
            reason=winning_reasons,
        )


# ---------------------------------------------------------------------------
# Factory entry point used by base._instantiate()
# ---------------------------------------------------------------------------


def build_adapter(*, role: LLMRole, settings: Settings) -> LLMAdapter:
    return OpenAILLMAdapter(role=role, settings=settings)


__all__ = ["OpenAILLMAdapter", "build_adapter"]
