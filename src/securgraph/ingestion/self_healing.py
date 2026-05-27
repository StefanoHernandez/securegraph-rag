"""Self-healing module for Knowledge Graph relations discrepancy reconciliation."""

from __future__ import annotations

import json
from typing import Any

from neo4j import AsyncDriver

from securgraph.adapters.neo4j_writer import flag_or_delete_relationship
from securgraph.adapters.ollama import get_ollama_client
from securgraph.config import get_settings
from securgraph.logging import get_logger
from securgraph.observability.otel import GRAPH_SELF_HEALING_COUNT

_LOG = get_logger(__name__)


async def resolve_graph_contradiction(
    driver: AsyncDriver,
    tenant_id: str,
    claim_text: str,
    vector_context: str,
) -> dict[str, Any] | None:
    """Prompt the LLM judge to reconcile a graph claim contradiction against vector ground truth.

    Applies the repair transaction to Neo4j and increments observability metrics.
    """
    _LOG.info(
        "graph_self_healing_started",
        tenant_id=tenant_id,
        claim_text=claim_text[:60],
    )
    client = get_ollama_client()
    settings = get_settings()

    system_prompt = (
        "You are an expert graph reconciliation assistant.\n"
        "A knowledge graph relation extracted from the text has been flagged as a CONTRADICTION to the original document context.\n"
        "You must identify:\n"
        "1. The source entity name.\n"
        "2. The incorrect target entity name.\n"
        "3. The corrected target entity name (if any, directly from the true context).\n"
        "4. The recommended action: \"delete\" (relationship is completely false/hallucinated), "
        "\"update\" (relationship exists but has an incorrect target/value), or \"flag\" (uncertain).\n\n"
        "Output strictly in JSON matching this schema:\n"
        "{\n"
        '  "source_entity": "subject name",\n'
        '  "incorrect_target_entity": "wrong object name",\n'
        '  "corrected_target_entity": "true object name or null",\n'
        '  "action": "delete | update | flag",\n'
        '  "reason": "Explain why this action is taken based on the true context."\n'
        "}\n"
        "Do not write markdown blocks or text, return ONLY the raw JSON."
    )

    prompt = (
        f"Wrong Graph Claim: {claim_text}\n"
        f"True Vector Context: {vector_context}"
    )

    try:
        response = await client.chat(
            model=settings.llm_model_judge,
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
        
        source = data.get("source_entity")
        incorrect_target = data.get("incorrect_target_entity")
        corrected_target = data.get("corrected_target_entity")
        action = data.get("action")
        reason = data.get("reason") or "Self-healing guided reconciliation."

        if not source or not incorrect_target or action not in ("delete", "update", "flag"):
            _LOG.warning(
                "graph_self_healing_invalid_json",
                tenant_id=tenant_id,
                payload=data,
            )
            return None

        _LOG.info(
            "graph_self_healing_resolved",
            tenant_id=tenant_id,
            source=source,
            incorrect_target=incorrect_target,
            action=action,
            corrected_target=corrected_target,
        )

        # Apply repair to Neo4j
        await flag_or_delete_relationship(
            driver=driver,
            tenant_id=tenant_id,
            source_name=source,
            target_name=incorrect_target,
            action=action,
            new_target_name=corrected_target,
            reason=reason,
        )

        # Increment Prometheus metric
        GRAPH_SELF_HEALING_COUNT.labels(tenant_id=tenant_id, action=action).inc()

        return {
            "source_entity": source,
            "incorrect_target_entity": incorrect_target,
            "corrected_target_entity": corrected_target,
            "action": action,
            "reason": reason,
        }

    except Exception as e:
        _LOG.exception("graph_self_healing_failed", tenant_id=tenant_id, error=str(e))
        return None
