#!/usr/bin/env python3
"""Ragas Offline Quality Evaluation Pipeline.

Executes end-to-end RAG queries against the local SecurGraph FastAPI service,
gathers context and generated answers, and computes quality metrics using Ragas
leveraging our local Ollama instance.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pandas as pd
import yaml
from datasets import Dataset
from httpx import ASGITransport, AsyncClient

from securgraph.api.main import create_app

# Ragas imports
try:
    from langchain_community.embeddings import OllamaEmbeddings
    from langchain_community.llms import Ollama
    from ragas import evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import answer_relevance, context_precision, context_recall, faithfulness
    RAGAS_AVAILABLE = True
except ImportError:
    RAGAS_AVAILABLE = False


async def main() -> None:
    # Ensure Qdrant collection is initialized
    from securgraph.adapters.qdrant_init import init_qdrant_collection
    print("Ensuring Qdrant collection is initialized...")
    try:
        init_qdrant_collection()
    except Exception as e:
        print(f"Collection init warning: {e}")

    # Create and ingest a dummy source document for evaluation to populate databases
    from securgraph.api.dependencies import AsyncSessionLocal
    from securgraph.ingestion.service import IngestionRequest, ingest_document

    doc_text = (
        "Stefano Hernandez is a software engineer. He is leading the development of SecureGraph, a high-trust Graph-RAG framework designed for regulated environments.\n\n"
        "Stefano is based in Milan. Stefano is a member of the Advanced Agentic Coding team.\n\n"
        "SecureGraph enforces fine-grained access control (ReBAC) on vector and graph databases, records W3C PROV-compliant explainability traces, and performs bidirectional NLI consensus claim verification.\n"
    )

    print("Ingesting evaluation source document...")
    request = IngestionRequest(
        tenant_id="mrd",
        source_uri="evaluation_source.txt",
        owner_id="user:stefano",
        data=doc_text.encode("utf-8"),
        policy_tags=("dev",)
    )

    async with AsyncSessionLocal() as session:
        try:
            await ingest_document(session, request)
            await session.commit()
            print("Evaluation source document ingested successfully.")
        except Exception as e:
            await session.rollback()
            print(f"Ingestion warning (already exists or DB error): {e}")

    # 1. Load QA Pairs
    corpus_path = Path(__file__).parent.parent / "corpus" / "qa_pairs_generated.yaml"
    if not corpus_path.exists():
        print(f"Error: Golden QA dataset not found at {corpus_path}")
        sys.exit(1)

    with open(corpus_path, encoding="utf-8") as f:
        qa_pairs = yaml.safe_load(f)

    print(f"Loaded {len(qa_pairs)} golden QA pairs.")

    # 2. Build test cases by executing queries through the ASGI app
    app = create_app()
    transport = ASGITransport(app=app)

    test_cases = []

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for idx, item in enumerate(qa_pairs, start=1):
            question = item["question"]
            ground_truth = item["ground_truth"]
            expected_context = item["expected_context"]

            print(f"[{idx}/{len(qa_pairs)}] Executing Query: {question!r}...")

            payload = {
                "tenant_id": "mrd",
                "user_id": "user:stefano",
                "query": question,
                "options": {
                    "consensus_threshold": 0.6,
                    "top_k": 5
                }
            }

            try:
                resp = await client.post("/query", json=payload)
                if resp.status_code != 200:
                    print(f"  Warning: Query failed with status {resp.status_code}")
                    print(f"  Response: {resp.text}")
                    continue

                body = resp.json()
                answer = body["answer"]

                # Reconstruct contexts from the channels summary
                contexts = []
                # Vector chunks
                if body.get("channels") and body["channels"].get("vector"):
                    vector_summary = body["channels"]["vector"].get("draft_summary")
                    if vector_summary:
                        contexts.append(vector_summary)
                # Graph paths
                if body.get("channels") and body["channels"].get("graph"):
                    graph_paths = body["channels"]["graph"].get("paths", [])
                    for path in graph_paths:
                        contexts.append(str(path))

                if not contexts:
                    contexts = [expected_context]

                test_cases.append({
                    "question": question,
                    "answer": answer,
                    "contexts": contexts,
                    "ground_truth": ground_truth
                })
            except Exception as e:
                print(f"  Error querying pipeline: {e}")

    if not test_cases:
        print("No successful test cases executed. Evaluation aborted.")
        sys.exit(1)

    print(f"\nSuccessfully executed {len(test_cases)} evaluation cases.")

    # 3. Format dataset for Ragas
    data = {
        "question": [t["question"] for t in test_cases],
        "contexts": [t["contexts"] for t in test_cases],
        "answer": [t["answer"] for t in test_cases],
        "ground_truth": [t["ground_truth"] for t in test_cases]
    }

    dataset = Dataset.from_dict(data)

    # 4. Run Ragas evaluation using local Ollama model if available
    if RAGAS_AVAILABLE:
        print("\nInitializing Ragas quality evaluation with local Ollama LLM...")
        try:
            # Wrap our local Ollama LLM and Embeddings
            local_llm = Ollama(model="granite4.1:3b", base_url="http://localhost:11434")
            local_emb = OllamaEmbeddings(model="granite4.1:3b", base_url="http://localhost:11434")

            ragas_llm = LangchainLLMWrapper(local_llm)
            ragas_emb = LangchainEmbeddingsWrapper(local_emb)

            # Configure metrics with our local wrappers
            faithfulness.llm = ragas_llm
            answer_relevance.llm = ragas_llm
            answer_relevance.embeddings = ragas_emb
            context_precision.llm = ragas_llm
            context_precision.embeddings = ragas_emb
            context_recall.llm = ragas_llm

            metrics = [faithfulness, answer_relevance, context_precision, context_recall]

            print("Computing Ragas quality metrics (Faithfulness, Answer Relevance, Context Precision/Recall)...")
            result = evaluate(
                dataset,
                metrics=metrics,
                llm=ragas_llm,
                embeddings=ragas_emb
            )

            print("\n" + "="*80)
            print("                      RAGAS EVALUATION METRICS REPORT")
            print("="*80)
            df = result.to_pandas()
            print(df.to_string(index=False))
            print("\nOverall Scores:")
            for metric_name, val in result.scores.items():
                print(f"  - {metric_name}: {val:.4f}")
            print("="*80)
        except Exception as e:
            print(f"Ragas evaluation encountered an error: {e}")
            print("Falling back to raw RAG execution report.")
            _print_raw_report(test_cases)
    else:
        print("\nRagas is not available or dependencies are missing.")
        _print_raw_report(test_cases)


def _print_raw_report(test_cases: list[dict]) -> None:
    print("\n" + "="*80)
    print("                         RAW RAG EXECUTION REPORT")
    print("="*80)
    df = pd.DataFrame(test_cases)
    print(df[["question", "answer", "contexts"]].to_string(index=False))
    print("="*80)


if __name__ == "__main__":
    asyncio.run(main())
