# Artifact reproduction (submission track)

Reproduce MRD dev and Helios held-out evaluation from a clean stack.

## Prerequisites

- Docker, Python 3.11, `uv`, Mistral + OpenAI API keys in `.env`
- No Ollama in the acceptance path

## Bootstrap

```bash
make nuke && make up && make migrate
.venv/bin/python -c "from securgraph.adapters.qdrant_init import init_qdrant_collection; init_qdrant_collection()"
```

## Dev corpus (`mrd_v1`)

Terminal A — API with dev baseline templates (mrd_v1 only):

```bash
SECURGRAPH_AUTH_ENABLED=false \
SECURGRAPH_EVAL_IDENTITY_MODE=true \
SECURGRAPH_EVAL_BASELINE_TEMPLATES=1 \
SECURGRAPH_RATELIMIT_QUERY=0 \
make api
```

Terminal B:

```bash
make mrd-seed-live
make mrd-live
```

Reports: `eval/results/mrd-qa-smoke-*.json`

## Held-out corpus (`mrd_heldout_v1`)

Restart API **without** `SECURGRAPH_EVAL_BASELINE_TEMPLATES`.

```bash
make heldout-seed-live
make heldout-live   # run twice; security metrics must match
```

Reports: `eval/results/mrd-heldout-*.json`

## Offline re-score

```bash
.venv/bin/python eval/mrd_qa_smoke.py \
  --corpus corpus/mrd_v1 \
  --results-json eval/results/mrd-qa-smoke-<stamp>.json
```

## Submit-track gates

```bash
make ci
make test-adversarial
pytest tests/unit/test_tamper_audit_v2.py -q
```
