"""Ollama async client adapter for structured graph extraction.

Uses local Ollama with JSON schema forcing to extract entities and relations.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from ollama import AsyncClient
from pydantic import BaseModel, Field

from securgraph.config import get_settings
from securgraph.domain.graph import ExtractedGraphSlice
from securgraph.logging import get_logger

_LOG = get_logger(__name__)


@lru_cache(maxsize=1)
def get_ollama_client() -> AsyncClient:
    """Process-wide cached Ollama AsyncClient instance."""
    settings = get_settings()
    return AsyncClient(host=settings.ollama_url)


async def extract_graph_slice(text: str) -> ExtractedGraphSlice:
    """Extract entities and relations from a text chunk using a local LLM.

    Ensures robust parsing by stripping out markdown blocks and wraps errors in a
    safe empty-slice fallback to prevent crashing the ingestion pipeline.
    """
    settings = get_settings()
    client = get_ollama_client()

    system_prompt = (
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

    try:
        response = await client.chat(
            model=settings.llm_model_extract,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            format="json",
            options={"num_ctx": settings.llm_num_ctx},
        )

        content = response.message.content
        if not content:
            return ExtractedGraphSlice(entities=[], relations=[])

        # Strip markdown code blocks if any (fallback for older LLM outputs)
        content_stripped = content.strip()
        if content_stripped.startswith("```"):
            lines = content_stripped.splitlines()
            if lines[0].startswith("```json") or lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content_stripped = "\n".join(lines).strip()

        data = json.loads(content_stripped)
        return ExtractedGraphSlice.model_validate(data)

    except Exception as e:
        _LOG.warning("ollama_extraction_failed", error=str(e), text_sample=text[:100])
        # Graceful fallback to prevent breaking the caller
        return ExtractedGraphSlice(entities=[], relations=[])


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
            options={"num_ctx": settings.llm_num_ctx},
        )
        content = response.message.content or "{}"
        
        # Robust parsing: strip potential markdown wrapper
        content_stripped = content.strip()
        if content_stripped.startswith("```"):
            lines = content_stripped.splitlines()
            if lines[0].startswith("```json") or lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content_stripped = "\n".join(lines).strip()

        data = json.loads(content_stripped)

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
    """Evaluate claim against reference text using live Ollama NLI classification."""
    client = get_ollama_client()
    settings = get_settings()

    system_prompt = (
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

    prompt = (
        f"Claim: {claim}\n"
        f"Reference Context: {reference}"
    )

    try:
        response = await client.chat(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            format="json",
            options={"num_ctx": settings.llm_num_ctx}
        )
        content = response.message.content or "{}"
        
        # Robust parsing: strip potential markdown wrapper
        content_stripped = content.strip()
        if content_stripped.startswith("```"):
            lines = content_stripped.splitlines()
            if lines[0].startswith("```json") or lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content_stripped = "\n".join(lines).strip()

        data = json.loads(content_stripped)

        # Normalize keys
        label = data.get("label")
        if not label:
            # Maybe the model returned {"entailment": "true"} or similar
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

        reason = data.get("reason") or "NLI evaluation completed."

        return NLIResult(label=label, confidence=confidence, reason=reason)

    except Exception as e:
        _LOG.warning("judge_nli_failed", error=str(e), claim=claim[:50])
        return NLIResult(label="neutral", confidence=0.0, reason=f"Failed to judge: {str(e)}")
