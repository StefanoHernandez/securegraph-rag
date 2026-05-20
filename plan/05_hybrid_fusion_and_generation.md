# Milestone 5: Hybrid Fusion & Generation Spec

This milestone covers routing incoming queries based on classified intent, merging multiple candidate rankings using Reciprocal Rank Fusion (RRF), performing dynamic token budgeting, and generating answers with precise inline citations using local Ollama (`gemma3:1b`).

---

## 1. Query Intent Classification

We classify incoming user queries to route them to the most efficient retrieval channel.

| Query Classification | Retrieval Route | Focus |
|---|---|---|
| `FACTUAL_SEMANTIC` | Vector | Dense semantic meaning |
| `PATH_TRAVERSAL` | Graph | Multi-hop relationships between concepts |
| `HYBRID` | Vector + Graph | Combination of context and structure |

### 📝 Query Router (`src/securgraph/domain/query.py`)

```python
from __future__ import annotations
from enum import StrEnum

class QueryClassification(StrEnum):
    FACTUAL_SEMANTIC = "factual_semantic"
    PATH_TRAVERSAL = "path_traversal"
    HYBRID = "hybrid"

def classify_query(query_text: str) -> QueryClassification:
    """Classify intent of incoming query using simple rule-based keywords."""
    query = query_text.lower()
    graph_keywords = {"related to", "connected", "relationship", "how does", "link", "between"}
    
    if any(k in query for k in graph_keywords):
        return QueryClassification.PATH_TRAVERSAL
    return QueryClassification.FACTUAL_SEMANTIC
```

---

## 2. Reciprocal Rank Fusion (RRF)

RRF merges rank lists from dense vector search and full-text lexical search without needing score calibration.

$$RRF\_Score(d) = \sum_{m \in M} \frac{1}{k + r_m(d)}$$

### 📝 Python RRF Merger (`src/securgraph/indexing/fusion.py`)

```python
from __future__ import annotations
from collections import defaultdict
from securgraph.domain import RetrievalEvidence

def fuse_rankings(
    dense_results: list[RetrievalEvidence],
    lexical_results: list[RetrievalEvidence],
    k: int = 60,
    limit: int = 5,
) -> list[RetrievalEvidence]:
    """Execute Reciprocal Rank Fusion (RRF) over two evidence lists."""
    rrf_scores: dict[str, float] = defaultdict(float)
    evidence_map: dict[str, RetrievalEvidence] = {}

    # 1. Score dense results
    for rank, ev in enumerate(dense_results):
        key = ev.object_id
        rrf_scores[key] += 1.0 / (k + (rank + 1))
        evidence_map[key] = ev

    # 2. Score lexical results
    for rank, ev in enumerate(lexical_results):
        key = ev.object_id
        rrf_scores[key] += 1.0 / (k + (rank + 1))
        # Keep denser evidence as default if present
        if key not in evidence_map:
            evidence_map[key] = ev

    # 3. Sort by fused score
    sorted_keys = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)[:limit]
    
    fused_evidence = []
    for rank, key in enumerate(sorted_keys):
        base_ev = evidence_map[key]
        # Create updated evidence containing fused rank and score
        fused_evidence.append(
            RetrievalEvidence(
                evidence_id=base_ev.evidence_id,
                trace_id=base_ev.trace_id,
                tenant_id=base_ev.tenant_id,
                evidence_type=base_ev.evidence_type,
                object_id=base_ev.object_id,
                retriever_id=base_ev.retriever_id,
                retrieval_strategy="rrf_fused",
                original_rank=rank,
                original_score=rrf_scores[key],
                included_in_prompt=True,
            )
        )
    return fused_evidence
```

---

## 3. Token Budgeting & Context Assembly

Using `tiktoken`, we measure chunk lengths and assemble context dynamically, guaranteeing we stay within local memory bounds.

```python
import tiktoken
from securgraph.domain import Chunk

def assemble_prompt_context(
    authorized_chunks: list[Chunk],
    max_tokens: int = 3000,
) -> tuple[str, dict[str, str]]:
    """Assemble context. Returns prompt block and mapping of evidence IDs."""
    encoder = tiktoken.get_encoding("cl100k_base")
    context_blocks = []
    evidence_id_map = {}
    current_tokens = 0

    for idx, c in enumerate(authorized_chunks):
        block = f"[Source {idx+1}]: {c.text}\n"
        tokens = len(encoder.encode(block))
        if current_tokens + tokens > max_tokens:
            break  # Fit under budget
            
        context_blocks.append(block)
        evidence_id_map[f"Source {idx+1}"] = c.chunk_id
        current_tokens += tokens
        
    return "\n".join(context_blocks), evidence_id_map
```

---

## 4. LLM Generation Adapter & Citation System

Answers are generated using local `gemma3:1b` with explicit instructions to generate inline citations.

### 📝 Dynamic Prompt Structure

```
You are a highly secure corporate assistant. Answer the user's question using ONLY the provided sources below.
If the sources do not contain the answer, state that you do not know. Do not hallucinate.

Cite your sources in the text using [Source X] format.

---
SOURCES:
{context_block}
---

USER QUESTION: {user_query}
ANSWER:
```

### 📝 Citation Post-Processor

The post-processor converts raw `[Source X]` citations in the model output into verifiable system `evidence_id` objects returned in the final `/query` response contract.
