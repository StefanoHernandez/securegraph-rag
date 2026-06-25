from __future__ import annotations

import asyncio
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from securgraph.adapters.llm import get_llm_adapter, get_llm_adapter_for
from securgraph.api.dependencies import AsyncSessionLocal
from securgraph.domain import (
    Answer,
    Claim,
    ConsensusReport,
    NLILabel,
    Subject,
    VerifierStatus,
)
from securgraph.domain.consensus import calculate_consensus
from securgraph.logging import get_logger
from securgraph.observability import get_tracer
from securgraph.runtime_overrides import get_effective_settings

_LOG = get_logger(__name__)

# Strong references to fire-and-forget background tasks. asyncio only keeps a
# weak reference to a bare create_task() result, so without this a self-healing
# task could be garbage-collected mid-run and silently cancelled (RUF006).
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _bounded_context(items: list[Any], token_budget: int) -> str:
    """Join evidence/path strings for a draft prompt, bounded by a token budget.

    A broad graph query can return many paths whose reconstructed text overlaps,
    so an unbounded ``"\\n".join(str(ev) ...)`` can blow past the model's context
    window (observed: ~1M tokens). Deduplicate identical fragments and stop once
    the budget is reached (~4 chars/token heuristic).
    """
    char_budget = max(1, token_budget) * 4
    seen: set[str] = set()
    parts: list[str] = []
    used = 0
    dropped = 0
    for ev in items:
        text = str(ev).strip()
        if not text or text in seen:
            continue
        if used + len(text) > char_budget:
            dropped += 1
            continue
        seen.add(text)
        parts.append(text)
        used += len(text) + 1
    if dropped:
        _LOG.info("draft_context_truncated", dropped=dropped, token_budget=token_budget)
    return "\n".join(parts)


def _heuristic_seeds(query: str) -> list[str]:
    """Fallback seed extraction: capitalized words stripped of trailing punctuation."""
    return [word.strip(",.?!") for word in query.split() if word and word[0].isupper()]


async def _extract_seeds_from_catalog(driver: Any, tenant_id: str, query: str) -> list[str]:
    """Extract seed entity names by substring-matching the query against the Neo4j entity catalog.

    Strategy:
      1. Pull all (e:Entity {tenant_id}) names from Neo4j.
      2. Bidirectional case-insensitive substring match:
         - entity_name.lower() in query.lower()  -> include entity_name
         - query_token.lower() in entity_name.lower() (token len >= 3) -> include entity_name
      3. Deduplicate while preserving canonical (catalog) form.
      4. If the catalog is empty (cold start) or any error occurs, fall back to the
         capitalized-word heuristic.
    """
    try:
        async with driver.session() as session:
            result = await session.run(
                "MATCH (e:Entity) WHERE e.tenant_id = $tid RETURN e.name AS name",
                tid=tenant_id,
            )
            records = await result.data()
    except Exception as exc:  # pragma: no cover - degrade gracefully on infra error
        _LOG.warning("seed_catalog_fetch_failed", error=str(exc))
        return _heuristic_seeds(query)

    entity_names = [r["name"] for r in records if r.get("name")]
    if not entity_names:
        return _heuristic_seeds(query)

    query_lower = query.lower()
    tokens = [t.strip(",.?!:;()[]\"'").lower() for t in query.split()]
    tokens = [t for t in tokens if len(t) >= 3]

    seeds: list[str] = []
    seen: set[str] = set()
    for name in entity_names:
        if not isinstance(name, str):
            continue
        name_lower = name.lower()
        match = name_lower in query_lower or any(tok in name_lower for tok in tokens)
        if match and name_lower not in seen:
            seeds.append(name)
            seen.add(name_lower)

    return seeds


class QueryState(TypedDict):
    query: str
    subject: Subject
    options: dict[str, Any]
    trace_id: str
    vector_context: list[Any]
    vector_draft: str | None
    vector_claims: list[Claim]
    graph_context: list[Any]
    graph_draft: str | None
    graph_claims: list[Claim]
    consensus_report: ConsensusReport | None
    final_answer: Answer | None
    warnings: list[str]


def compile_workflow(qdrant: Any, graph: Any) -> Any:
    builder = StateGraph(QueryState)

    def _mode(state: QueryState) -> str:
        m = state["options"].get("retrieval_mode") or "auto"
        return "hybrid" if m == "auto" else m

    async def vector_retrieval_node(state: QueryState) -> dict[str, Any]:
        if state["options"].get("query_type") == "global":
            return {"vector_context": []}
        mode = _mode(state)
        if mode == "graph":
            return {"vector_context": []}
        tracer = get_tracer()
        with tracer.start_as_current_span("vector_retrieval") as span:
            subject: Subject = state["subject"]
            q: str = state["query"]
            trace: str = state["trace_id"]
            limit = state["options"].get("top_k", 10)

            if mode == "lexical":
                dense = []
            else:
                dense = await qdrant.search_dense(subject.tenant_id, q, limit=limit, trace_id=trace)
            lexical = await qdrant.search_lexical(subject.tenant_id, q, limit=limit, trace_id=trace)

            from securgraph.indexing.fusion import fuse_rankings

            fused = fuse_rankings(dense, lexical, limit=limit)

            # ReBAC fail-closed BEFORE evidence reaches synthesis: filter the
            # fused list through OpenFGA so an authenticated tenant member who
            # is not a viewer of a specific chunk never sees it surface from
            # the vector path. Without this, multi-tenant scoping alone would
            # leak intra-tenant chunks across users.
            fga = getattr(graph, "fga", None)
            denied_count = 0
            if fga is not None and fused:
                chunk_ids = [ev.object_id for ev in fused if getattr(ev, "object_id", None)]
                authorized_ids, decisions = await fga.filter_chunk_ids(
                    subject=subject,
                    chunk_ids=chunk_ids,
                    trace_id=trace,
                )
                filtered = [ev for ev in fused if ev.object_id in authorized_ids]
                denied_count = len(fused) - len(filtered)
                fused = filtered

                # Audit persistence is part of the authorization decision. If
                # it cannot be committed, abort before evidence reaches synthesis.
                from securgraph.indexing.postgres_writer import write_policy_decisions

                async with AsyncSessionLocal() as session:
                    await write_policy_decisions(session, decisions)
                    await session.commit()

            span.set_attribute("securgraph.dense_count", len(dense))
            span.set_attribute("securgraph.lexical_count", len(lexical))
            span.set_attribute("securgraph.fused_count", len(fused))
            span.set_attribute("securgraph.vector_denied_count", denied_count)
            return {"vector_context": fused}

    async def graph_retrieval_node(state: QueryState) -> dict[str, Any]:
        tracer = get_tracer()
        with tracer.start_as_current_span("graph_retrieval") as span:
            if state["options"].get("query_type") == "global":
                from securgraph.adapters.neo4j import get_neo4j_driver
                from securgraph.indexing.global_retriever import AuthorizedCommunityRetriever

                retriever = AuthorizedCommunityRetriever(
                    driver=getattr(graph, "driver", None) or get_neo4j_driver(),
                    fga=getattr(graph, "fga", None),  # type: ignore[arg-type]
                )

                summaries = await retriever.retrieve_communities(
                    subject=state["subject"],
                    level=1,  # Target Level 1 (high-level themes)
                    trace_id=state["trace_id"],
                )
                span.set_attribute("securgraph.communities_count", len(summaries))

                context_parts = []
                for idx, comm in enumerate(summaries):
                    context_parts.append(
                        f"--- Community Cluster {idx}: {comm.title} (Level {comm.level}) ---\n"
                        f"Summary: {comm.summary}\n"
                        f"Members: {', '.join(comm.member_entities)}\n"
                    )
                global_context = "\n".join(context_parts)
                return {"graph_context": summaries, "graph_draft": global_context}

            mode = _mode(state)
            if mode in ("vector", "lexical"):
                return {"graph_context": []}

            driver = getattr(graph, "driver", None)
            if driver is None:
                from securgraph.adapters.neo4j import get_neo4j_driver

                driver = get_neo4j_driver()
            seeds = await _extract_seeds_from_catalog(
                driver=driver,
                tenant_id=state["subject"].tenant_id,
                query=state["query"],
            )
            async with AsyncSessionLocal() as session:
                paths = await graph.traverse_authorized_subgraph(
                    session=session,
                    subject=state["subject"],
                    seed_entities=seeds,
                    trace_id=state["trace_id"],
                    max_depth=state["options"].get("max_graph_depth", 2),
                )
                await session.commit()

            span.set_attribute("securgraph.seeds_count", len(seeds))
            span.set_attribute("securgraph.paths_count", len(paths))
            return {"graph_context": paths}

    async def vector_draft_node(state: QueryState) -> dict[str, Any]:
        if state["options"].get("query_type") == "global":
            return {"vector_draft": ""}
        if not state["vector_context"]:
            return {"vector_draft": ""}
        tracer = get_tracer()
        with tracer.start_as_current_span("vector_draft"):
            adapter = get_llm_adapter(tenant_id=state["subject"].tenant_id)
            settings = get_effective_settings(state["subject"].tenant_id)
            context_text = _bounded_context(
                list(state["vector_context"]), settings.query_token_budget
            )
            prompt = (
                "Answer using vector sources only:\n"
                f"Context: {context_text}\nQuery: {state['query']}"
            )

            content = await adapter.chat(
                system=(
                    "You are a secure factual RAG assistant. Output a concise answer "
                    "based ONLY on the context. If no context matches or is provided, "
                    "state that there is no available information."
                ),
                user=prompt,
                think=settings.llm_thinking,
                keep_alive=300,
            )
            return {"vector_draft": content}

    async def graph_draft_node(state: QueryState) -> dict[str, Any]:
        if state["options"].get("query_type") == "global":
            return {}
        if not state["graph_context"]:
            return {"graph_draft": ""}
        tracer = get_tracer()
        with tracer.start_as_current_span("graph_draft"):
            adapter = get_llm_adapter(tenant_id=state["subject"].tenant_id)
            settings = get_effective_settings(state["subject"].tenant_id)
            context_text = _bounded_context(
                list(state["graph_context"]), settings.query_token_budget
            )
            prompt = (
                "Answer using graph sources only:\n"
                f"Context: {context_text}\nQuery: {state['query']}"
            )

            content = await adapter.chat(
                system=(
                    "You are a secure factual RAG assistant. Output a concise answer "
                    "based ONLY on the context. If no context matches or is provided, "
                    "state that there is no available information."
                ),
                user=prompt,
                think=settings.llm_thinking,
                keep_alive=300,
            )
            return {"graph_draft": content}

    async def vector_claim_extraction_node(state: QueryState) -> dict[str, Any]:
        if state["options"].get("query_type") == "global" or not state["vector_draft"]:
            return {"vector_claims": []}
        decomp = await get_llm_adapter(
            tenant_id=state["subject"].tenant_id
        ).extract_claims(state["vector_draft"])
        claims = [
            Claim(
                claim_id=c.claim_id,
                text=c.text,
                supporting_evidence_ids=tuple(),
                nli_label=None,
                verifier_status=VerifierStatus.NOT_CHECKED,
            )
            for c in decomp.claims
        ]
        return {"vector_claims": claims}

    async def graph_claim_extraction_node(state: QueryState) -> dict[str, Any]:
        if state["options"].get("query_type") == "global" or not state["graph_draft"]:
            return {"graph_claims": []}
        decomp = await get_llm_adapter(
            tenant_id=state["subject"].tenant_id
        ).extract_claims(state["graph_draft"])
        claims = [
            Claim(
                claim_id=c.claim_id,
                text=c.text,
                supporting_evidence_ids=tuple(),
                nli_label=None,
                verifier_status=VerifierStatus.NOT_CHECKED,
            )
            for c in decomp.claims
        ]
        return {"graph_claims": claims}

    async def consensus_verifier_node(state: QueryState) -> dict[str, Any]:
        if state["options"].get("query_type") == "global":
            return {"consensus_report": None, "vector_claims": [], "graph_claims": []}
        tracer = get_tracer()
        with tracer.start_as_current_span("consensus_verifier") as span:
            v_claims = list(state["vector_claims"])
            g_claims = list(state["graph_claims"])

            # Run live NLI judgments bidirectionally
            nli_adapter = get_llm_adapter_for("judge", tenant_id=state["subject"].tenant_id)
            updated_v_claims = []
            for c in v_claims:
                res = await nli_adapter.judge_nli(c.text, state["graph_draft"] or "")
                updated_v_claims.append(
                    c.model_copy(
                        update={
                            "nli_label": NLILabel(res.label),
                            "nli_confidence": res.confidence,
                            "verifier_status": VerifierStatus.SUPPORTED
                            if res.label == "entailment"
                            else VerifierStatus.UNSUPPORTED,
                        }
                    )
                )

            updated_g_claims = []
            for c in g_claims:
                res = await nli_adapter.judge_nli(c.text, state["vector_draft"] or "")
                updated_g_claims.append(
                    c.model_copy(
                        update={
                            "nli_label": NLILabel(res.label),
                            "nli_confidence": res.confidence,
                            "verifier_status": VerifierStatus.SUPPORTED
                            if res.label == "entailment"
                            else VerifierStatus.UNSUPPORTED,
                        }
                    )
                )

            # Extract entity sets from claims text (longer words to avoid stop words)
            vector_entities = set()
            for c in updated_v_claims:
                vector_entities.update(
                    [w.lower().strip(",.?!") for w in c.text.split() if len(w) > 4]
                )
            graph_entities = set()
            for c in updated_g_claims:
                graph_entities.update(
                    [w.lower().strip(",.?!") for w in c.text.split() if len(w) > 4]
                )

            report = calculate_consensus(
                trace_id=state["trace_id"],
                vector_claims=tuple(updated_v_claims),
                graph_claims=tuple(updated_g_claims),
                vector_entities=vector_entities,
                graph_entities=graph_entities,
                threshold=state["options"].get("consensus_threshold", 0.7),
            )

            # Check if self-healing is enabled and if there are graph contradictions
            settings = get_effective_settings(state["subject"].tenant_id)
            if settings.feature_self_healing_enabled:
                for c in updated_g_claims:
                    if c.nli_label == NLILabel.CONTRADICTION:
                        _LOG.info(
                            "contradiction_detected_triggering_self_healing",
                            claim=c.text[:60],
                            trace_id=state["trace_id"],
                        )
                        driver = getattr(graph, "driver", None)
                        if driver is None:
                            from securgraph.adapters.neo4j import get_neo4j_driver

                            driver = get_neo4j_driver()

                        from securgraph.ingestion.self_healing import resolve_graph_contradiction

                        # Run self-healing as a background task. Hold a strong
                        # reference until it finishes so the loop can't GC it.
                        _healing_task = asyncio.create_task(
                            resolve_graph_contradiction(
                                driver=driver,
                                tenant_id=state["subject"].tenant_id,
                                claim_text=c.text,
                                vector_context=state["vector_draft"] or "",
                            )
                        )
                        _BACKGROUND_TASKS.add(_healing_task)
                        _healing_task.add_done_callback(_BACKGROUND_TASKS.discard)

            span.set_attribute("securgraph.agreement_score", report.agreement_score)
            span.set_attribute("securgraph.high_trust", report.high_trust)
            return {
                "consensus_report": report,
                "vector_claims": updated_v_claims,
                "graph_claims": updated_g_claims,
            }

    async def synthesis_node(state: QueryState) -> dict[str, Any]:
        tracer = get_tracer()
        with tracer.start_as_current_span("synthesis"):
            adapter = get_llm_adapter(tenant_id=state["subject"].tenant_id)
            settings = get_effective_settings(state["subject"].tenant_id)

            if state["options"].get("query_type") == "global":
                final_prompt = (
                    "You are a global synthesis agent. Synthesize a comprehensive, "
                    "high-level, structured answer answering the query based on the "
                    "following Hierarchical Graph Community Summaries:\n"
                    f"{state['graph_draft']}\n\n"
                    f"Query: {state['query']}"
                )
                content = await adapter.chat(
                    system=(
                        "You are a global synthesis agent. Produce a comprehensive, "
                        "secure, structured answer summarizing the community summaries. "
                        "Do not extrapolate outside the context."
                    ),
                    user=final_prompt,
                    think=settings.llm_thinking,
                    keep_alive=300,
                )
                return {"final_answer": Answer(text=content, citations=tuple())}

            final_prompt = (
                f"Synthesize a final unified secure response based on two draft reviews:\n"
                f"Vector Draft: {state['vector_draft']}\n"
                f"Graph Draft: {state['graph_draft']}\n"
                "Agreement Score: "
                f"{state['consensus_report'].agreement_score if state['consensus_report'] else 0.0}"
            )
            content = await adapter.chat(
                system=(
                    "You are a synthesis agent. Produce a secure, unified answer grounded "
                    "strictly in both drafts. Do not extrapolate, only summarize agreement "
                    "areas. If there is no agreement and the score is below the threshold, "
                    "warn that agreement could not be reached and synthesize a cautious "
                    "minimal response."
                ),
                user=final_prompt,
                think=settings.llm_thinking,
                keep_alive=300,
            )
            return {"final_answer": Answer(text=content, citations=tuple())}

    # Add all nodes
    builder.add_node("vector_retrieval", vector_retrieval_node)
    builder.add_node("graph_retrieval", graph_retrieval_node)
    builder.add_node("vector_draft", vector_draft_node)
    builder.add_node("graph_draft", graph_draft_node)
    builder.add_node("vector_claims", vector_claim_extraction_node)
    builder.add_node("graph_claims", graph_claim_extraction_node)
    builder.add_node("consensus_verifier", consensus_verifier_node)
    builder.add_node("synthesis", synthesis_node)

    # Build sequential parallel channels
    builder.add_edge(START, "vector_retrieval")
    builder.add_edge(START, "graph_retrieval")
    builder.add_edge("vector_retrieval", "vector_draft")
    builder.add_edge("graph_retrieval", "graph_draft")
    builder.add_edge("vector_draft", "vector_claims")
    builder.add_edge("graph_draft", "graph_claims")
    builder.add_edge("vector_claims", "consensus_verifier")
    builder.add_edge("graph_claims", "consensus_verifier")
    builder.add_edge("consensus_verifier", "synthesis")
    builder.add_edge("synthesis", END)

    return builder.compile()
