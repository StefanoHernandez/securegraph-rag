# Milestone 7: Observability & Answer Verification Spec

This milestone instruments OpenTelemetry (OTel) distributed tracing across all pipeline boundaries (FastAPI, Qdrant, Neo4j, OpenFGA, Ollama), exports traces to Jaeger, tracks Prometheus metrics, and implements a local Natural Language Inference (NLI) answer verifier to detect hallucinations.

---

## 1. OpenTelemetry Distributed Tracing & Jaeger

We instrument the pipeline so every request maps to a single distributed trace. 

### ⚙ OTel Tracing Architecture

```
FastAPI Server Span [0.0.0.0:8000]
 ├── route: /query [parent]
 │    ├── span: query_classification
 │    ├── span: qdrant_vector_retrieval (httpx)
 │    ├── span: openfga_authorization (httpx)
 │    ├── span: neo4j_graph_traversal (bolt)
 │    ├── span: reciprocal_rank_fusion
 │    ├── span: ollama_generation (httpx)
 │    └── span: answer_consensus_verification
```

### 📝 OpenTelemetry Setup Helper (`src/securgraph/observability/otel.py`)

```python
from __future__ import annotations

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource

from securgraph.config import get_settings

def setup_observability(settings) -> None:
    if not settings.otel_enabled:
        return

    resource = Resource.create(attributes={
        "service.name": settings.service_name,
        "service.environment": settings.env,
    })

    provider = TracerProvider(resource=resource)
    processor = BatchSpanProcessor(
        OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
    )
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

def get_tracer() -> trace.Tracer:
    return trace.get_tracer("securgraph")
```

### 📝 Manual Span Instrumenting Example

```python
from securgraph.observability.otel import get_tracer

tracer = get_tracer()

async def search_pipeline(tenant_id: str, query: str):
    with tracer.start_as_current_span("search_pipeline") as span:
        span.set_attribute("securgraph.tenant_id", tenant_id)
        # Pipeline execution goes here...
```

---

## 2. Cross-Retriever Consensus (Answer Verifier)

To meet **Commitment 2: Cross-Retriever Consensus**, we run a post-generation verification step. We extract claims from the generated answer and verify each claim against the authorized chunks using Natural Language Inference (NLI).

### 📝 Structured Verification Schema (`src/securgraph/domain/consensus.py`)

```python
from __future__ import annotations
from enum import StrEnum
from pydantic import BaseModel, Field

class EntailmentVerdict(StrEnum):
    SUPPORTED = "supported"
    REFUTED = "refuted"
    NEUTRAL = "neutral"

class AtomicClaim(BaseModel):
    claim_text: str = Field(description="The extracted factual statement")
    verdict: EntailmentVerdict = Field(description="NLI verdict relative to the source context")
    supporting_evidence_ids: list[str] = Field(
        default_factory=list, 
        description="IDs of source chunks that back this claim"
    )

class ConsensusReport(BaseModel):
    trace_id: str
    claims: list[AtomicClaim] = Field(default_factory=list)
    agreement_score: float = Field(description="Percentage of supported claims", ge=0.0, le=1.0)
    high_trust: bool = Field(description="True if agreement_score >= 0.8")
```

### 📝 Verification Flow Prompt

We instruct local `gemma3:1b` to evaluate if a generated claim is logically entailed by the retrieved evidence chunks.

```
Evaluate if the CLAIM is supported by the provided CONTEXT.
Answer with: "SUPPORTED", "REFUTED", or "NEUTRAL".

CONTEXT:
{context_block}

CLAIM:
{claim_text}

VERDICT:
```

If any claim is `REFUTED`, the verifier flags the response, downgrades `agreement_score`, sets `high_trust = False`, and triggers an audit warning event in the trace log.
