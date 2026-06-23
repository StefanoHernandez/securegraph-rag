"""Google Gemini LLM adapter.

Implements :class:`securgraph.adapters.llm.base.LLMAdapter` against the
Google Gemini API (``google-genai`` SDK, the unified client for Gemini 2.x
and later). Gemini supports native structured-JSON output via
``response_mime_type="application/json"``, which we use for every structured
stage (extract / claims / NLI) so the parser path matches the OpenAI adapter.

Security note: same caveat as the OpenAI adapter — only authorized evidence
is sent to the cloud provider. Use the per-role config to keep extraction on
Ollama if the source corpus is sensitive.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

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
    robust_json_loads,
)
from securgraph.config import Settings
from securgraph.domain.graph import ExtractedGraphSlice
from securgraph.logging import get_logger

_LOG = get_logger(__name__)


@lru_cache(maxsize=2)
def _get_genai_client(api_key: str) -> Any:
    """Cached google-genai client. Import is lazy so ``google-genai`` remains
    an optional dependency."""
    from google import genai

    return genai.Client(api_key=api_key)


def _normalize_extracted_slice(data: dict[str, Any]) -> ExtractedGraphSlice:
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


class GoogleLLMAdapter(LLMAdapter):
    """Google Gemini implementation of the LLMAdapter ABC."""

    provider_name = "google"

    def __init__(self, role: LLMRole, settings: Settings) -> None:
        self._role = role
        self._settings = settings
        if role == "extract":
            self._model = settings.llm_model_extract
        elif role == "judge":
            self._model = settings.llm_model_judge
        else:
            self._model = settings.llm_model
        api_key = settings.google_api_key.get_secret_value()
        if not api_key:
            raise RuntimeError(
                "Google adapter selected but GOOGLE_API_KEY is not set. "
                "Either set the key or switch SECURGRAPH_LLM_PROVIDER back to "
                "'ollama'."
            )
        self._client = _get_genai_client(api_key)

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
        from google.genai.types import GenerateContentConfig

        # The google-genai SDK accepts a system instruction via config.
        # ``response_mime_type="application/json"`` enforces structured output.
        config = GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            response_mime_type="application/json" if json_mode else None,
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user,
            config=config,
        )
        # The SDK exposes the text via .text on the response object.
        return getattr(response, "text", None) or ""

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
            _LOG.warning("google_extraction_failed", error=str(e), text_sample=text[:100])
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
            _LOG.warning("google_extract_claims_failed", error=str(e), text_sample=text[:100])
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
            _LOG.warning("google_nli_vote_failed", error=str(e), temperature=temperature)
            return NLIResult(label="neutral", confidence=0.0, reason=f"Failed: {e!s}")

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


def build_adapter(*, role: LLMRole, settings: Settings) -> LLMAdapter:
    return GoogleLLMAdapter(role=role, settings=settings)


__all__ = ["GoogleLLMAdapter", "build_adapter"]
