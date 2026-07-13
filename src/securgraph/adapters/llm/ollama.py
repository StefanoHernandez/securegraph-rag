"""Ollama LLM adapter — legacy local-development provider.

Implements :class:`securgraph.adapters.llm.base.LLMAdapter` over the official
``ollama.AsyncClient``. It is a legacy local-development provider selected
only explicitly; the submission and production defaults use Mistral for
generation/extraction and OpenAI for NLI judging.

The module preserves the per-stage ``keep_alive`` discipline introduced for
the baseline harness:
    - extract_graph_slice uses ``keep_alive=0`` (unload immediately; the
      offline ingestion phase is the only place where the heavy extract model
      runs, and we want the VRAM back before the online query phase).
    - extract_claims / judge_nli / generation keep the model warm for
      ``keep_alive=300`` seconds in an explicitly configured legacy
      local-development deployment.

``build_adapter`` is the entry point used by the factory in ``base.py``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from ollama import AsyncClient

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
from securgraph.config import Settings, get_settings
from securgraph.domain.graph import ExtractedGraphSlice
from securgraph.logging import get_logger

_LOG = get_logger(__name__)


# ---------------------------------------------------------------------------
# Client cache — one AsyncClient per process (the SDK is connection-pooled).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_ollama_client() -> AsyncClient:
    """Process-wide cached Ollama AsyncClient instance."""
    return AsyncClient(host=get_settings().ollama_url)


# ---------------------------------------------------------------------------
# Helpers shared by all Ollama call sites.
# ---------------------------------------------------------------------------


def _normalize_extracted_slice(data: dict[str, Any]) -> ExtractedGraphSlice:
    """Validate entities/relations one-by-one so a single bad item doesn't
    drop the entire slice. A relation missing ``confidence`` defaults to 0.5.
    """
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


def _merge_slices(a: ExtractedGraphSlice, b: ExtractedGraphSlice) -> ExtractedGraphSlice:
    """Union entities by lowercase name (first occurrence wins). Concat relations."""
    by_name: dict[str, Any] = {}
    for e in list(a.entities) + list(b.entities):
        key = (e.name or "").strip().lower()
        if key and key not in by_name:
            by_name[key] = e
    return ExtractedGraphSlice(
        entities=list(by_name.values()),
        relations=list(a.relations) + list(b.relations),
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class OllamaLLMAdapter(LLMAdapter):
    """Ollama implementation of the LLMAdapter ABC."""

    provider_name = "ollama"

    def __init__(self, role: LLMRole, settings: Settings) -> None:
        self._role = role
        self._settings = settings
        if role == "extract":
            self._model = settings.llm_model_extract
            self._think = settings.llm_thinking_extract
        elif role == "judge":
            self._model = settings.llm_model_judge
            self._think = settings.llm_thinking
        else:
            self._model = settings.llm_model
            self._think = settings.llm_thinking

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
        client = get_ollama_client()
        response = await client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format="json" if json_mode else "",
            options={
                "num_ctx": self._settings.llm_num_ctx,
                "temperature": temperature,
            },
            think=self._think if think is None else think,
            keep_alive=keep_alive if keep_alive is not None else 300,
        )
        return response.message.content or ""

    # ----- extract_graph_slice ----------------------------------------------

    async def _extract_once(self, text: str, temperature: float) -> ExtractedGraphSlice:
        content = await self.chat(
            system=EXTRACT_SYSTEM_PROMPT,
            user=text,
            json_mode=True,
            temperature=temperature,
            think=self._think,
            keep_alive=0,
        )
        data = robust_json_loads(content)
        return _normalize_extracted_slice(data)

    async def extract_graph_slice(self, text: str) -> ExtractedGraphSlice:
        """Extract entities and relations from a text chunk using a local LLM.

        Small JSON-strict models occasionally emit ``{"entities":[...],
        "relations":[]}`` even when relations are obviously present. When
        entities are present but relations are empty, we re-issue the same
        prompt with a small temperature bump and merge the two results.
        All other failures collapse to an empty slice to keep ingestion alive.
        """
        try:
            first = await self._extract_once(text, temperature=0.0)
        except Exception as e:
            _LOG.warning("ollama_extraction_failed", error=str(e), text_sample=text[:100])
            return ExtractedGraphSlice(entities=[], relations=[])

        if len(first.entities) < 3 or first.relations:
            return first

        _LOG.warning(
            "extract_zero_relations_retrying",
            n_entities=len(first.entities),
            model=self._model,
        )
        try:
            second = await self._extract_once(text, temperature=0.2)
        except Exception as e:
            _LOG.warning("extract_retry_failed", error=str(e), text_sample=text[:100])
            return first
        return _merge_slices(first, second)

    # ----- extract_claims ---------------------------------------------------

    async def extract_claims(self, text: str) -> ClaimDecomposition:
        try:
            content = await self.chat(
                system=CLAIM_DECOMPOSITION_SYSTEM_PROMPT,
                user=text,
                json_mode=True,
                temperature=0.0,
                think=self._think,
                keep_alive=300,
            )
            data = robust_json_loads(content)
        except Exception as e:
            _LOG.warning("extract_claims_failed", error=str(e), text_sample=text[:100])
            return ClaimDecomposition(claims=[])

        normalized_claims: list[AtomicClaim] = []
        if isinstance(data, dict) and "claims" in data and isinstance(data["claims"], list):
            for idx, item in enumerate(data["claims"]):
                if not isinstance(item, dict):
                    continue
                cid = item.get("claim_id") or item.get("id") or f"c{idx}"
                ctext = item.get("text") or item.get("claim") or item.get("content") or ""
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
                    normalized_claims.append(
                        AtomicClaim(
                            claim_id=str(cid),
                            text=str(ctext),
                            entities=entities,
                        )
                    )
        return ClaimDecomposition(claims=normalized_claims)

    # ----- judge_nli --------------------------------------------------------

    async def _execute_single_nli_vote(
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
                think=self._think,
                keep_alive=300,
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
            _LOG.warning("single_nli_vote_failed", error=str(e), temperature=temperature)
            return NLIResult(label="neutral", confidence=0.0, reason=f"Failed: {e!s}")

    async def judge_nli(self, claim: str, reference: str) -> NLIResult:
        prompt = f"Claim: {claim}\nReference Context: {reference}"

        if not self._settings.feature_verifier_ensemble:
            return await self._execute_single_nli_vote(NLI_SYSTEM_PROMPT_V1, prompt, 0.0)

        import asyncio

        votes = await asyncio.gather(
            self._execute_single_nli_vote(NLI_SYSTEM_PROMPT_V1, prompt, 0.0),
            self._execute_single_nli_vote(NLI_SYSTEM_PROMPT_V2, prompt, 0.3),
            self._execute_single_nli_vote(NLI_SYSTEM_PROMPT_V3, prompt, 0.7),
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
                # Tie breaker: prefer neutral/fail-safe
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
    return OllamaLLMAdapter(role=role, settings=settings)


__all__ = ["OllamaLLMAdapter", "build_adapter", "get_ollama_client"]
