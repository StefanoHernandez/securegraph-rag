"""Ollama async client adapter for structured graph extraction.

Uses local Ollama with JSON schema forcing to extract entities and relations.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal

from ollama import AsyncClient
from pydantic import BaseModel, Field

from securgraph.config import get_settings
from securgraph.domain.graph import ExtractedGraphSlice
from securgraph.logging import get_logger

_LOG = get_logger(__name__)


import re

def _robust_json_loads(content: str) -> Any:
    """Robust JSON parser that strips markdown fences and removes trailing commas."""
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

    # Remove trailing commas before closing braces/brackets
    content_stripped = re.sub(r',(\s*[}\]])', r'\1', content_stripped)

    return json.loads(content_stripped)


@lru_cache(maxsize=1)
def get_ollama_client() -> AsyncClient:
    """Process-wide cached Ollama AsyncClient instance."""
    return AsyncClient(host=get_settings().ollama_url)


_EXTRACT_SYSTEM_PROMPT = (
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


async def _extract_once(text: str, temperature: float = 0.0) -> ExtractedGraphSlice:
    """Single extract call. Parses JSON, normalizes, returns the slice."""
    settings = get_settings()
    client = get_ollama_client()

    response = await client.chat(
        model=settings.llm_model_extract,
        messages=[
            {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        format="json",
        options={"num_ctx": settings.llm_num_ctx, "temperature": temperature},
        think=settings.llm_thinking_extract,
    )

    content = response.message.content or ""
    data = _robust_json_loads(content)
    return _normalize_extracted_slice(data)


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


async def extract_graph_slice(text: str) -> ExtractedGraphSlice:
    """Extract entities and relations from a text chunk using a local LLM.

    Small JSON-strict models (gemma4, granite) occasionally emit
    ``{"entities": [...], "relations": []}`` even when relations are
    obviously present in the source. The audit
    ``eval/results/extract_audit_20260611T105341Z.json`` measured this at
    ~67% of trials on gemma4 think=True. When entities are present but
    relations are empty, we re-issue the same prompt with a small
    temperature bump to draw a different sample and merge the two results
    (union entities by name, concat relations). All other failures
    (parse error, network exception) collapse to an empty slice to keep
    the ingestion pipeline alive.
    """
    try:
        first = await _extract_once(text, temperature=0.0)
    except Exception as e:
        _LOG.warning("ollama_extraction_failed", error=str(e), text_sample=text[:100])
        return ExtractedGraphSlice(entities=[], relations=[])

    if len(first.entities) < 3 or first.relations:
        return first

    settings = get_settings()
    _LOG.warning(
        "extract_zero_relations_retrying",
        n_entities=len(first.entities),
        model=settings.llm_model_extract,
    )
    try:
        second = await _extract_once(text, temperature=0.2)
    except Exception as e:
        _LOG.warning(
            "extract_retry_failed", error=str(e), text_sample=text[:100]
        )
        return first
    return _merge_slices(first, second)


def _normalize_extracted_slice(data: dict) -> ExtractedGraphSlice:
    """Validate entities/relations one-by-one so a single bad item doesn't
    drop the entire slice. Small LLMs occasionally emit a relation without
    ``confidence``; default to 0.5 (neutral) rather than throwing away the
    whole graph fragment for that chunk.
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


class AtomicClaim(BaseModel):
    claim_id: str = Field(description="Unique ID, e.g. 'c0', 'c1'")
    text: str = Field(description="Self-contained, atomic, factual claim.")
    entities: list[str] = Field(default_factory=list, description="Key entities mentioned in the claim.")


class ClaimDecomposition(BaseModel):
    claims: list[AtomicClaim]


class NLIResult(BaseModel):
    label: Literal["entailment", "neutral", "contradiction"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(description="Brief explanation.")


async def extract_claims(text: str) -> ClaimDecomposition:
    """Decompose text into atomic claims with entities using live Ollama structured JSON."""
    client = get_ollama_client()
    settings = get_settings()

    system_prompt = (
        "You are a precise claim decomposition assistant. Decompose the user's text into independent, atomic, self-contained factual claims and extract key entities.\n"
        "Output strictly in JSON matching this schema:\n"
        "{\n"
        '  "claims": [\n'
        '    {\n'
        '      "claim_id": "c0",\n'
        '      "text": "The factual claim statement.",\n'
        '      "entities": ["entity1", "entity2"]\n'
        '    }\n'
        '  ]\n'
        "}\n"
        "If no claims are found, return an empty list under 'claims'. Do not write markdown blocks or text, return ONLY the raw JSON."
    )

    try:
        response = await client.chat(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            format="json",
            options={"num_ctx": settings.llm_num_ctx, "temperature": 0.0},
            think=settings.llm_thinking,
        )
        content = response.message.content or "{}"

        data = _robust_json_loads(content)

        # Robust normalizer in case the LLM used different keys/structures
        normalized_claims = []
        if isinstance(data, dict) and "claims" in data and isinstance(data["claims"], list):
            for idx, item in enumerate(data["claims"]):
                if not isinstance(item, dict):
                    continue
                # Normalize claim_id
                cid = item.get("claim_id") or item.get("id") or f"c{idx}"
                # Normalize text
                ctext = item.get("text") or item.get("claim") or item.get("content") or ""
                # Normalize entities
                raw_entities = item.get("entities") or []
                entities = []
                if isinstance(raw_entities, list):
                    for ent in raw_entities:
                        if isinstance(ent, str):
                            entities.append(ent)
                        elif isinstance(ent, dict) and "entity" in ent:
                            entities.append(ent["entity"])
                        elif isinstance(ent, dict) and "name" in ent:
                            entities.append(ent["name"])

                if ctext:
                    normalized_claims.append({
                        "claim_id": str(cid),
                        "text": str(ctext),
                        "entities": entities
                    })

        return ClaimDecomposition(claims=[AtomicClaim(**c) for c in normalized_claims])

    except Exception as e:
        _LOG.warning("extract_claims_failed", error=str(e), text_sample=text[:100])
        return ClaimDecomposition(claims=[])


async def judge_nli(claim: str, reference: str) -> NLIResult:
    """Evaluate claim against reference text using live Ollama NLI classification.

    If settings.feature_verifier_ensemble is enabled, performs a 3-voter majority consensus.
    """
    client = get_ollama_client()
    settings = get_settings()

    async def _execute_single_nli_vote(
        system_prompt: str,
        user_prompt: str,
        temperature: float,
    ) -> NLIResult:
        try:
            response = await client.chat(
                model=settings.llm_model_judge,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                format="json",
                options={"num_ctx": settings.llm_num_ctx, "temperature": temperature},
                think=settings.llm_thinking,
            )
            content = response.message.content or "{}"
            data = _robust_json_loads(content)

            # Normalize keys
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
            return NLIResult(label="neutral", confidence=0.0, reason=f"Failed: {str(e)}")

    system_prompt_v1 = (
        "You are an expert NLI judge classifying relationships. Analyze whether the Claim is supported by the Reference Context.\n"
        "Classify the relationship as exactly one of: entailment, neutral, or contradiction.\n"
        "Output strictly in JSON matching this schema:\n"
        "{\n"
        '  "label": "entailment",\n'
        '  "confidence": 0.95,\n'
        '  "reason": "Explain why this label was chosen."\n'
        "}\n"
        "Do not write markdown blocks or text, return ONLY the raw JSON."
    )

    system_prompt_v2 = (
        "You are a highly conservative and strict NLI judge. Analyze whether the Claim is fully and explicitly supported by the Reference Context.\n"
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

    system_prompt_v3 = (
        "You are an NLI judge focused on detecting factual support or contradiction.\n"
        "Check if the Claim directly contradicts the Context (contradiction) or is fully validated by it (entailment), else classify as neutral.\n"
        "Classify exactly as: entailment, neutral, or contradiction.\n"
        "Output strictly in JSON matching this schema:\n"
        "{\n"
        '  "label": "contradiction",\n'
        '  "confidence": 0.95,\n'
        '  "reason": "Explain choice."\n'
        "}\n"
        "Do not write markdown blocks or text, return ONLY the raw JSON."
    )

    prompt = (
        f"Claim: {claim}\n"
        f"Reference Context: {reference}"
    )

    if not settings.feature_verifier_ensemble:
        # Fall back to single voter
        return await _execute_single_nli_vote(system_prompt_v1, prompt, temperature=0.0)

    import asyncio
    # Run 3 voters concurrently!
    votes = await asyncio.gather(
        _execute_single_nli_vote(system_prompt_v1, prompt, temperature=0.0),
        _execute_single_nli_vote(system_prompt_v2, prompt, temperature=0.3),
        _execute_single_nli_vote(system_prompt_v3, prompt, temperature=0.7),
    )

    # Perform majority vote
    counts = {"entailment": 0, "neutral": 0, "contradiction": 0}
    for v in votes:
        counts[v.label] += 1

    # Find the winning label (majority of 3 votes)
    winning_label = "neutral"
    max_count = -1
    for label, count in counts.items():
        if count > max_count:
            max_count = count
            winning_label = label
        elif count == max_count:
            # Tie breaker: prefer neutral/fail-safe
            if label == "neutral" or (label == "contradiction" and winning_label == "entailment"):
                winning_label = label

    # Compute average confidence for the winning label voters
    winning_confidences = [v.confidence for v in votes if v.label == winning_label]
    if not winning_confidences:
        # If no voters voted for the winner (not possible in 3 votes, but let's be safe)
        winning_confidences = [v.confidence for v in votes]
    avg_confidence = sum(winning_confidences) / len(winning_confidences)

    winning_reasons = "; ".join([f"Voter: {v.reason}" for v in votes if v.label == winning_label])

    return NLIResult(label=winning_label, confidence=round(avg_confidence, 4), reason=winning_reasons)
