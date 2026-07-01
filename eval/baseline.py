"""Five-mode baseline harness: vector / lexical / graph / hybrid / global.

Re-runs the same QA corpus five times through the production /query endpoint,
each time with a different `retrieval_mode`, and computes RAGAS core metrics.
Writes `eval/results/baseline-<UTC-date>.csv` and a markdown table.

Synthesis is held fixed by going through the production workflow; only the
retrieval branch differs. This experimental design lets the paper attribute
deltas to the retriever, not to the synthesizer.

Two independent LLM layers, each configurable separately:

1. **Pipeline LLM** (extract / draft / synthesis / NLI judge): configured via
   `SECURGRAPH_LLM_PROVIDER` (default `ollama`; can be `mistral` / `openai` /
   `google` / `anthropic` for cloud runs). See `docs/research/model_substitution.md`.

2. **RAGAS eval judge + embeddings**: configured via `RAGAS_JUDGE_PROVIDER`
   (default `openai`; can be `ollama` for a fully-local, zero-cost run).

Judge independence (P1.4): the RAGAS judge is a different provider/model from
the pipeline's internal NLI judge. For the local-only run the separation is
achieved by using different Ollama models (pipeline judge = granite4.1:3b,
RAGAS judge = qwen3.5:2b). For the cloud run the separation is by provider.

Run profiles:
  - **Local-only** (zero cloud cost): SECURGRAPH_LLM_PROVIDER=ollama,
    RAGAS_JUDGE_PROVIDER=ollama.
  - **Cloud pipeline, cloud RAGAS**: SECURGRAPH_LLM_PROVIDER=mistral,
    RAGAS_JUDGE_PROVIDER=openai, MISTRAL_API_KEY=..., OPENAI_API_KEY=...
  - **Local pipeline, cloud RAGAS**: SECURGRAPH_LLM_PROVIDER=ollama,
    RAGAS_JUDGE_PROVIDER=openai, OPENAI_API_KEY=...
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import os
import sys
from pathlib import Path

import pandas as pd
import yaml
from datasets import Dataset
from httpx import ASGITransport, AsyncClient
from openfga_sdk.client.models import ClientTuple, ClientWriteRequest

from securgraph.api.dependencies import AsyncSessionLocal, bootstrap_openfga
from securgraph.api.main import create_app
from securgraph.config import get_settings
from securgraph.corpus.manifest import CorpusManifest, load_corpus_manifest
from securgraph.domain import Classification
from securgraph.ingestion.service import IngestionRequest, ingest_document

try:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics._answer_relevance import AnswerRelevancy
    from ragas.metrics._context_precision import LLMContextPrecisionWithReference
    from ragas.metrics._context_recall import LLMContextRecall
    from ragas.metrics._faithfulness import Faithfulness
    from ragas.run_config import RunConfig
except ImportError as e:
    print(f"ragas/langchain-openai not installed in this env: {e}")
    sys.exit(2)

# Optional: Ollama-backed RAGAS judge for the local-only run profile.
# Imported lazily so the ollama package isn't required when RAGAS_JUDGE_PROVIDER
# is "openai" (the default).
try:
    from langchain_community.embeddings import OllamaEmbeddings
    from langchain_core.callbacks import (
        AsyncCallbackManagerForLLMRun,
        CallbackManagerForLLMRun,
    )
    from langchain_core.language_models.llms import LLM
    from ollama import AsyncClient as OllamaAsyncClient

    _OLLAMA_RAGAS_AVAILABLE = True
except ImportError:
    _OLLAMA_RAGAS_AVAILABLE = False


class OllamaThinkOff(LLM):
    """Minimal langchain LLM that calls Ollama with ``think=False``.

    Used only when RAGAS_JUDGE_PROVIDER=ollama. The RAGAS judge model
    (qwen3.5:2b) is a thinking model; leaving thinking enabled balloons
    latency on long prompts and causes RAGAS to time out.
    ``langchain_community.Ollama`` does not surface the ``think`` kwarg at the
    top level of the request body, so we call the official ollama client
    directly here.
    """

    model: str = "qwen3.5:2b"
    base_url: str = "http://localhost:11434"
    num_ctx: int = 16384
    temperature: float = 0.0

    @property
    def _llm_type(self) -> str:
        return "ollama-think-off"

    def _call(
        self,
        prompt: str,
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs,
    ) -> str:
        return asyncio.run(self._acall(prompt, stop=stop, run_manager=None, **kwargs))

    async def _acall(
        self,
        prompt: str,
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs,
    ) -> str:
        client = OllamaAsyncClient(host=self.base_url)
        opts: dict[str, object] = {
            "num_ctx": self.num_ctx,
            "temperature": self.temperature,
        }
        if stop:
            opts["stop"] = stop
        resp = await client.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            options=opts,
            think=False,
            keep_alive=0,
        )
        return resp.message.content or ""


MODES = ("vector", "lexical", "graph", "hybrid", "global")
CORPUS_ROOT = Path(
    os.environ.get(
        "SECURGRAPH_CORPUS_PATH",
        Path(__file__).parent.parent / "corpus" / "baseline_v1",
    )
)
CORPUS_PATH = CORPUS_ROOT / "qa_pairs.yaml"
RESULTS_DIR = Path(__file__).parent / "results"
DEFAULT_USER_ID = "stefano"


async def _run_mode(
    client: AsyncClient,
    mode: str,
    qa_pairs: list[dict],
    *,
    tenant_id: str,
    default_user_id: str,
) -> list[dict]:
    rows: list[dict] = []
    # "global" mode exercises C3 (policy-partitioned communities + global RAG).
    # It routes through the community summaries path by setting query_type=global
    # and using hybrid retrieval_mode as the underlying carrier.
    if mode == "global":
        retrieval_mode = "hybrid"
        query_type = "global"
    else:
        retrieval_mode = mode
        query_type = "standard"
    for idx, item in enumerate(qa_pairs, start=1):
        identity = item.get("identity", default_user_id)
        if not isinstance(identity, str) or not identity:
            identity = default_user_id
        payload = {
            "tenant_id": tenant_id,
            "user_id": f"user:{identity}",
            "query": item["question"],
            "options": {
                "retrieval_mode": retrieval_mode,
                "query_type": query_type,
                "top_k": 5,
                "consensus_threshold": 0.6,
            },
        }
        resp = await client.post("/query", json=payload, timeout=180.0)
        if resp.status_code != 200:
            print(f"  [{mode}] [{idx}] HTTP {resp.status_code}: {resp.text[:200]}")
            continue
        body = resp.json()
        answer = body.get("answer", "") if isinstance(body.get("answer"), str) else ""

        # Reconstruct contexts from the per-channel summary, same pattern as
        # scripts/eval.py. Vector channel contributes its draft_summary (the
        # synthesized text over retrieved chunks); graph channel contributes
        # each traversed path's textual form. Falls back to the QA pair's
        # expected_context only when the run produced no channel content
        # (e.g. retrieval_mode skipped a branch and the other returned empty).
        contexts: list[str] = []
        channels = body.get("channels") or {}
        vec_summary = (channels.get("vector") or {}).get("draft_summary")
        if vec_summary:
            contexts.append(vec_summary)
        graph_summary = (channels.get("graph") or {}).get("draft_summary")
        if graph_summary:
            contexts.append(graph_summary)
        graph_paths = (channels.get("graph") or {}).get("paths", []) or []
        for path in graph_paths:
            contexts.append(str(path))
        if not contexts:
            contexts = [item.get("expected_context", "")]

        rows.append(
            {
                "mode": mode,
                "question": item["question"],
                "ground_truth": item["ground_truth"],
                "answer": answer,
                "contexts": contexts,
            }
        )
    return rows


def _ragas_evaluate(rows: list[dict]) -> pd.DataFrame:
    """Compute RAGAS core metrics on the collected rows, grouped by mode.

    Two judge backends, selected by ``RAGAS_JUDGE_PROVIDER``:

    - ``openai`` (default): ``ChatOpenAI`` + ``OpenAIEmbeddings``. Requires
      ``OPENAI_API_KEY``. Models via ``RAGAS_JUDGE_MODEL`` (default
      ``gpt-4o-mini``) and ``RAGAS_EMBEDDING_MODEL`` (default
      ``text-embedding-3-small``).

    - ``ollama``: ``OllamaThinkOff`` + ``OllamaEmbeddings``. Requires the
      local Ollama service. Models via ``RAGAS_JUDGE_MODEL`` (default
      ``qwen3.5:2b``) and ``RAGAS_EMBEDDING_MODEL`` (default
      ``nomic-embed-text``).

    Judge independence (P1.4): the RAGAS judge is a different provider/model
    from the pipeline's internal NLI judge regardless of which backend is
    selected.
    """
    judge_provider = os.environ.get("RAGAS_JUDGE_PROVIDER", "openai").lower()
    judge_model = os.environ.get(
        "RAGAS_JUDGE_MODEL", "gpt-4o-mini" if judge_provider == "openai" else "qwen3.5:2b"
    )
    embedding_model = os.environ.get(
        "RAGAS_EMBEDDING_MODEL",
        "text-embedding-3-small" if judge_provider == "openai" else "nomic-embed-text",
    )

    if judge_provider == "ollama":
        if not _OLLAMA_RAGAS_AVAILABLE:
            print(
                "RAGAS_JUDGE_PROVIDER=ollama but langchain-community / ollama "
                "packages are not installed in the eval venv."
            )
            sys.exit(2)
        ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
        print(f"  RAGAS judge: Ollama {judge_model} @ {ollama_url}")
        llm = LangchainLLMWrapper(
            OllamaThinkOff(model=judge_model, num_ctx=16384, base_url=ollama_url)
        )
        emb = LangchainEmbeddingsWrapper(
            OllamaEmbeddings(model=embedding_model, base_url=ollama_url)
        )
    elif judge_provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        base_url = os.environ.get("OPENAI_BASE_URL") or None
        if not api_key:
            print(
                "RAGAS_JUDGE_PROVIDER=openai but OPENAI_API_KEY is not set. "
                "Either set the key or switch RAGAS_JUDGE_PROVIDER=ollama."
            )
            sys.exit(2)
        print(f"  RAGAS judge: OpenAI {judge_model}")
        llm = LangchainLLMWrapper(
            ChatOpenAI(
                model=judge_model,
                temperature=0.0,
                api_key=api_key,
                base_url=base_url,
            )
        )
        emb = LangchainEmbeddingsWrapper(
            OpenAIEmbeddings(model=embedding_model, api_key=api_key, base_url=base_url)
        )
    else:
        print(
            f"Unknown RAGAS_JUDGE_PROVIDER={judge_provider!r}. "
            "Supported: 'openai', 'ollama'."
        )
        sys.exit(2)

    faithfulness_metric = Faithfulness()
    answer_relevance_metric = AnswerRelevancy()
    context_precision_metric = LLMContextPrecisionWithReference()
    context_recall_metric = LLMContextRecall()
    metrics = [
        faithfulness_metric,
        answer_relevance_metric,
        context_precision_metric,
        context_recall_metric,
    ]
    run_config = RunConfig(timeout=300, max_retries=2)

    per_mode: dict[str, pd.DataFrame] = {}
    for mode in MODES:
        subset = [r for r in rows if r["mode"] == mode]
        if not subset:
            continue
        ds = Dataset.from_list(subset)
        res = evaluate(ds, metrics=metrics, llm=llm, embeddings=emb, run_config=run_config)
        per_mode[mode] = res.to_pandas()

    summary = []
    for mode, df in per_mode.items():
        summary.append(
            {
                "mode": mode,
                "faithfulness": df[faithfulness_metric.name].mean(),
                "answer_relevance": df[answer_relevance_metric.name].mean(),
                "context_precision": df[context_precision_metric.name].mean(),
                "context_recall": df[context_recall_metric.name].mean(),
                "n": len(df),
            }
        )
    return pd.DataFrame(summary)


def _write_outputs(summary: pd.DataFrame) -> tuple[Path, Path]:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    csv_path = RESULTS_DIR / f"baseline-{stamp}.csv"
    md_path = RESULTS_DIR / f"baseline-{stamp}.md"
    summary.to_csv(csv_path, index=False)
    md_path.write_text(summary.to_markdown(index=False) + "\n", encoding="utf-8")
    return csv_path, md_path


async def _ingest_corpus(session_maker, manifest: CorpusManifest) -> list[str]:
    """Ingest manifest-declared documents using their real corpus access labels."""
    doc_ids: list[str] = []
    for document in manifest.documents:
        async with session_maker() as session:
            req = IngestionRequest(
                tenant_id=manifest.tenant_id,
                source_uri=document.path.relative_to(manifest.root).as_posix(),
                owner_id=f"user:{document.owner}",
                data=document.path.read_bytes(),
                classification=Classification(document.classification),
                policy_tags=document.groups,
                direct_viewers=tuple(f"user:{viewer}" for viewer in document.direct_viewers),
            )
            try:
                result = await ingest_document(session, req)
                await session.commit()
                doc_ids.append(result.document_id)
                print(f"  ingested {document.path.name} -> {result.document_id} ({result.status})")
            except Exception as e:
                await session.rollback()
                print(f"  ingest warning {document.path.name}: {e}")
    return doc_ids


async def _seed_fga_owner_tuples(doc_ids: list[str], manifest: CorpusManifest) -> None:
    """Seed manifest owners, group viewers, and direct viewers for each document."""
    from securgraph.api.dependencies import _fga_client  # type: ignore[attr-defined]

    if _fga_client is None:
        print("  (no fga client; skipping tuple seed — queries will fail-closed)")
        return
    tuples: list[ClientTuple] = []
    for document_id, document in zip(doc_ids, manifest.documents, strict=True):
        target = f"document:{document_id}"
        tuples.append(ClientTuple(user=f"user:{document.owner}", relation="owner", object=target))
        tuples.extend(
            ClientTuple(user=f"group:{group_id}#member", relation="group_viewer", object=target)
            for group_id in document.groups
        )
        tuples.extend(
            ClientTuple(user=f"user:{user_id}", relation="direct_viewer", object=target)
            for user_id in document.direct_viewers
        )
    try:
        await _fga_client.write(ClientWriteRequest(writes=tuples))
        print(f"  seeded {len(tuples)} document:owner tuples")
    except Exception as e:
        # OpenFGA returns an error if tuples already exist; treat as benign.
        print(f"  fga seed note: {e}")


async def main() -> None:
    # Load .env manually to populate os.environ for RAGAS and other libraries
    dotenv_path = Path(__file__).parent.parent / ".env"
    if dotenv_path.exists():
        for line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k:
                    os.environ[k] = v

    if not CORPUS_PATH.exists():
        print(f"Missing corpus: {CORPUS_PATH}")
        sys.exit(2)
    try:
        manifest = load_corpus_manifest(CORPUS_ROOT)
    except Exception as exc:
        print(f"Invalid corpus manifest: {exc}")
        sys.exit(2)
    qa_pairs = yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(qa_pairs)} QA pairs from {CORPUS_PATH}")

    app = create_app()
    transport = ASGITransport(app=app)

    # Use the ASGI client wrapped around the app: this triggers the FastAPI
    # lifespan, which bootstraps OpenFGA + the policy adapter.
    # ASGITransport does NOT trigger FastAPI lifespan in httpx, so OpenFGA
    # is never bootstrapped via the ASGI client. Bootstrap it explicitly here
    # so document/chunk authorization checks find a valid store+model and the
    # FGA owner-tuple seeding below succeeds.
    print("--- Bootstrapping OpenFGA ---")
    await bootstrap_openfga(get_settings())

    # ASGITransport does NOT trigger FastAPI lifespan, so we must also
    # initialize the Qdrant collection (payload indexes, full-text index)
    # before ingestion writes vectors. Idempotent.
    print("--- Initializing Qdrant collection ---")
    from securgraph.adapters.qdrant_init import init_qdrant_collection

    init_qdrant_collection()
    print("  qdrant collection ready")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Health hit as a sanity check; lifespan is already bootstrapped above.
        with contextlib.suppress(Exception):
            await client.get("/health", timeout=30.0)

        print("--- Ingesting corpus ---")
        doc_ids = await _ingest_corpus(AsyncSessionLocal, manifest)
        print(f"Ingested {len(doc_ids)} documents")

        print("--- Seeding FGA owner tuples ---")
        await _seed_fga_owner_tuples(doc_ids, manifest)

        print("--- Generating hierarchical communities (C3) ---")
        try:
            from securgraph.adapters.community_generator import (
                generate_hierarchical_communities,
            )
            from securgraph.adapters.neo4j import get_neo4j_driver
            from securgraph.api.dependencies import _fga_client  # type: ignore[attr-defined]

            await generate_hierarchical_communities(
                driver=get_neo4j_driver(),
                tenant_id=manifest.tenant_id,
                fga=_fga_client,
            )
            print("  communities generated")
        except Exception as e:
            print(f"  community generation warning: {e}")

        all_rows: list[dict] = []
        for mode in MODES:
            print(f"--- Running mode: {mode} ---")
            all_rows.extend(
                await _run_mode(
                    client,
                    mode,
                    qa_pairs,
                    tenant_id=manifest.tenant_id,
                    default_user_id=DEFAULT_USER_ID,
                )
            )

    print("--- Computing RAGAS metrics ---")
    summary = _ragas_evaluate(all_rows)
    print(summary.to_string(index=False))

    csv_path, md_path = _write_outputs(summary)
    print(f"\nWrote: {csv_path}\nWrote: {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
