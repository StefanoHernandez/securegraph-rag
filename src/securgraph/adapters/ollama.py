"""Ollama async client adapter for structured graph extraction.

Uses local Ollama with JSON schema forcing to extract entities and relations.
"""

from __future__ import annotations

import json
from functools import lru_cache

from ollama import AsyncClient

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
