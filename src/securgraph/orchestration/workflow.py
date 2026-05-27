from __future__ import annotations

from typing import Any, TypedDict
from langgraph.graph import END, START, StateGraph

from securgraph.adapters.ollama import extract_claims, get_ollama_client, judge_nli
from securgraph.observability import get_tracer
from securgraph.api.dependencies import AsyncSessionLocal
from securgraph.config import get_settings
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

_LOG = get_logger(__name__)


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

    async def vector_retrieval_node(state: QueryState) -> dict[str, Any]:
        if state["options"].get("query_type") == "global":
            return {"vector_context": []}
        tracer = get_tracer()
        with tracer.start_as_current_span("vector_retrieval") as span:
            subject: Subject = state["subject"]
            q: str = state["query"]
            trace: str = state["trace_id"]
            limit = state["options"].get("top_k", 10)
            
            dense = await qdrant.search_dense(subject.tenant_id, q, limit=limit, trace_id=trace)
            lexical = await qdrant.search_lexical(subject.tenant_id, q, limit=limit, trace_id=trace)
            
            from securgraph.indexing.fusion import fuse_rankings
            fused = fuse_rankings(dense, lexical, limit=limit)
            
            span.set_attribute("securgraph.dense_count", len(dense))
            span.set_attribute("securgraph.lexical_count", len(lexical))
            span.set_attribute("securgraph.fused_count", len(fused))
            return {"vector_context": fused}

    async def graph_retrieval_node(state: QueryState) -> dict[str, Any]:
        tracer = get_tracer()
        with tracer.start_as_current_span("graph_retrieval") as span:
            if state["options"].get("query_type") == "global":
                from securgraph.adapters.neo4j import get_neo4j_driver
                from securgraph.indexing.global_retriever import AuthorizedCommunityRetriever
                
                retriever = AuthorizedCommunityRetriever(
                    driver=getattr(graph, "driver", None) or get_neo4j_driver(),
                    fga=getattr(graph, "fga", None),
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

            seeds = [word.strip(",.?!") for word in state["query"].split() if word and word[0].isupper()]
            async with AsyncSessionLocal() as session:
                paths = await graph.traverse_authorized_subgraph(
                    session=session,
                    subject=state["subject"],
                    seed_entities=seeds,
                    trace_id=state["trace_id"],
                    max_depth=state["options"].get("max_graph_depth", 2)
                )
            
            span.set_attribute("securgraph.seeds_count", len(seeds))
            span.set_attribute("securgraph.paths_count", len(paths))
            return {"graph_context": paths}

    async def vector_draft_node(state: QueryState) -> dict[str, Any]:
        if state["options"].get("query_type") == "global":
            return {"vector_draft": ""}
        tracer = get_tracer()
        with tracer.start_as_current_span("vector_draft"):
            client = get_ollama_client()
            settings = get_settings()
            context_text = "\n".join([str(ev) for ev in state["vector_context"]])
            prompt = f"Answer using vector sources only:\nContext: {context_text}\nQuery: {state['query']}"
            
            response = await client.chat(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": "You are a secure factual RAG assistant. Output a concise answer based ONLY on the context. If no context matches or is provided, state that there is no available information."},
                    {"role": "user", "content": prompt}
                ],
                options={"num_ctx": settings.llm_num_ctx}
            )
            return {"vector_draft": response.message.content or ""}

    async def graph_draft_node(state: QueryState) -> dict[str, Any]:
        if state["options"].get("query_type") == "global":
            return {}
        tracer = get_tracer()
        with tracer.start_as_current_span("graph_draft"):
            client = get_ollama_client()
            settings = get_settings()
            context_text = "\n".join([str(ev) for ev in state["graph_context"]])
            prompt = f"Answer using graph sources only:\nContext: {context_text}\nQuery: {state['query']}"
            
            response = await client.chat(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": "You are a secure factual RAG assistant. Output a concise answer based ONLY on the context. If no context matches or is provided, state that there is no available information."},
                    {"role": "user", "content": prompt}
                ],
                options={"num_ctx": settings.llm_num_ctx}
            )
            return {"graph_draft": response.message.content or ""}

    async def vector_claim_extraction_node(state: QueryState) -> dict[str, Any]:
        if state["options"].get("query_type") == "global" or not state["vector_draft"]:
            return {"vector_claims": []}
        decomp = await extract_claims(state["vector_draft"])
        claims = [
            Claim(
                claim_id=c.claim_id,
                text=c.text,
                supporting_evidence_ids=tuple(),
                nli_label=None,
                verifier_status=VerifierStatus.NOT_CHECKED
            ) for c in decomp.claims
        ]
        return {"vector_claims": claims}

    async def graph_claim_extraction_node(state: QueryState) -> dict[str, Any]:
        if state["options"].get("query_type") == "global" or not state["graph_draft"]:
            return {"graph_claims": []}
        decomp = await extract_claims(state["graph_draft"])
        claims = [
            Claim(
                claim_id=c.claim_id,
                text=c.text,
                supporting_evidence_ids=tuple(),
                nli_label=None,
                verifier_status=VerifierStatus.NOT_CHECKED
            ) for c in decomp.claims
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
            updated_v_claims = []
            for c in v_claims:
                res = await judge_nli(c.text, state["graph_draft"] or "")
                updated_v_claims.append(
                    c.model_copy(
                        update={
                            "nli_label": NLILabel(res.label),
                            "nli_confidence": res.confidence,
                            "verifier_status": VerifierStatus.SUPPORTED if res.label == "entailment" else VerifierStatus.UNSUPPORTED,
                        }
                    )
                )
                
            updated_g_claims = []
            for c in g_claims:
                res = await judge_nli(c.text, state["vector_draft"] or "")
                updated_g_claims.append(
                    c.model_copy(
                        update={
                            "nli_label": NLILabel(res.label),
                            "nli_confidence": res.confidence,
                            "verifier_status": VerifierStatus.SUPPORTED if res.label == "entailment" else VerifierStatus.UNSUPPORTED,
                        }
                    )
                )

            # Extract entity sets from claims text (longer words to avoid stop words)
            vector_entities = set()
            for c in updated_v_claims:
                vector_entities.update([w.lower().strip(",.?!") for w in c.text.split() if len(w) > 4])
            graph_entities = set()
            for c in updated_g_claims:
                graph_entities.update([w.lower().strip(",.?!") for w in c.text.split() if len(w) > 4])

            report = calculate_consensus(
                trace_id=state["trace_id"],
                vector_claims=tuple(updated_v_claims),
                graph_claims=tuple(updated_g_claims),
                vector_entities=vector_entities,
                graph_entities=graph_entities,
                threshold=state["options"].get("consensus_threshold", 0.7)
            )

            # Check if self-healing is enabled and if there are graph contradictions
            settings = get_settings()
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
                        import asyncio

                        # Run self-healing as a background task
                        asyncio.create_task(
                            resolve_graph_contradiction(
                                driver=driver,
                                tenant_id=state["subject"].tenant_id,
                                claim_text=c.text,
                                vector_context=state["vector_draft"] or "",
                            )
                        )
            
            span.set_attribute("securgraph.agreement_score", report.agreement_score)
            span.set_attribute("securgraph.high_trust", report.high_trust)
            return {"consensus_report": report, "vector_claims": updated_v_claims, "graph_claims": updated_g_claims}

    async def synthesis_node(state: QueryState) -> dict[str, Any]:
        tracer = get_tracer()
        with tracer.start_as_current_span("synthesis"):
            client = get_ollama_client()
            settings = get_settings()
            
            if state["options"].get("query_type") == "global":
                final_prompt = (
                    f"You are a global synthesis agent. Synthesize a comprehensive, high-level, structured answer "
                    f"answering the query based on the following Hierarchical Graph Community Summaries:\n"
                    f"{state['graph_draft']}\n\n"
                    f"Query: {state['query']}"
                )
                response = await client.chat(
                    model=settings.llm_model,
                    messages=[
                        {"role": "system", "content": "You are a global synthesis agent. Produce a comprehensive, secure, structured answer summarizing the community summaries. Do not extrapolate outside the context."},
                        {"role": "user", "content": final_prompt}
                    ],
                    options={"num_ctx": settings.llm_num_ctx}
                )
                return {"final_answer": Answer(text=response.message.content or "", citations=tuple())}

            final_prompt = (
                f"Synthesize a final unified secure response based on two draft reviews:\n"
                f"Vector Draft: {state['vector_draft']}\n"
                f"Graph Draft: {state['graph_draft']}\n"
                f"Agreement Score: {state['consensus_report'].agreement_score if state['consensus_report'] else 0.0}"
            )
            response = await client.chat(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": "You are a synthesis agent. Produce a secure, unified answer grounded strictly in both drafts. Do not extrapolate, only summarize agreement areas. If there is no agreement and the score is below the threshold, warn that agreement could not be reached and synthesize a cautious minimal response."},
                    {"role": "user", "content": final_prompt}
                ],
                options={"num_ctx": settings.llm_num_ctx}
            )
            return {"final_answer": Answer(text=response.message.content or "", citations=tuple())}

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
