# SecureGraph RAG — Health Report 2026-06-23

Branch: `feat/p02-baseline-harness` (20 commit avanti su `main`)
Verifica eseguita: 2026-06-23, durata effettiva ~30 min, modalità esaustiva.

## Sintesi

| Area | Stato | Note |
|---|---|---|
| Backend health (docker + /health) | ✅ PASS | 6/6 container healthy, 5/5 componenti `ok` |
| Test suite | ✅ PASS | 117/118 (94 unit + 22/23 integration + 1 adversarial); 1 flaky |
| API smoke — hybrid mode | ✅ PASS | `Maria Conti` corretto, agreement_score=1.0, high_trust=true |
| API smoke — graph mode | ✅ PASS | 55 paths trovati (fix catalog seed + FGA parent_document funzionano) |
| API smoke — single-channel modes (vector / lexical / graph alone) | ⚠️ DEGRADED | Hedge by-design `"Agreement could not be reached"`; nessun fact rivelato |
| API smoke — global mode | ✅ PASS | Risposta strutturata su community summaries |
| API smoke — `/explain/{trace_id}` | ✅ PASS | Schema W3C PROV-O (`prov_agents`, `prov_activities`, `prov_entities`, `evidence`) |
| API smoke — `/admin/audit/traces` | ✅ PASS | Lista 5 trace della sessione |
| API smoke — `/admin/permissions` | ✅ PASS | 35 tuple ritornate (fix `admin.py` SDK OpenFGA funzionante) |
| API smoke — `/admin/audit/verify-chain` | ❌ FAIL | `is_intact=false`, 5/5 query_traces e 112/112 retrieval_evidence "tampered" |
| ReBAC fail-closed (user non-owner) | ⚠️ DEGRADED | Fact "Maria Conti" NON rivelato ✓, ma `vec_ev=1` (vector channel non filtra a monte) |
| Frontend build (Next 16.2.9 Turbopack) | ✅ PASS | 12 route compilate (9 user-facing + `/_not-found`), 0 errori TS, 2.6s |
| Frontend SSR (curl ogni route) | ✅ PASS | 9/9 route HTTP 200 + `__next` hydration chunk |
| Frontend JS interactivity | ⚠️ NOT TESTED | Richiede browser umano (DevTools Network + console). Non simulabile da CLI |
| Static — lint (`ruff check`) | ❌ FAIL | 245 errori (E501 line-too-long 69x, W293 whitespace 50x, F401 unused 33x, I001 import-order 27x). Tech debt cosmetico |
| Static — type (`mypy`) | ❌ FAIL | 21 errori in 9 file. Bug reale: `query.py:50,51` usa `any` invece di `typing.Any` |

## Backend (PASS al livello funzionale, DEGRADED sulle proprietà di security)

### Infrastruttura

```
docker ps:  6/6 healthy
  securgraph-redis     healthy
  securgraph-openfga   up
  securgraph-postgres  healthy
  securgraph-qdrant    healthy
  securgraph-jaeger    healthy
  securgraph-neo4j     healthy

curl /health:  status=ok
  postgres: ok  (1.5ms)
  qdrant:   ok  (11.7ms)
  neo4j:    ok  (0.8ms)
  openfga:  ok  (2.6ms)
  ollama:   ok  (888ms)
```

API process: `eval/.venv/bin/uvicorn securgraph.api.main:app` su :8000 (il `.venv` root non esiste più; usato eval venv che ha securgraph installato editable). **Issue minore:** ricreare `.venv` root con `make venv` per consistenza con i Makefile target documentati.

### Test suite

| Suite | Pass | Note |
|---|---|---|
| `pytest tests/unit` | 94/94 | 11 warning (qdrant 1.18 client vs 1.12 server, async mock leftover) |
| `pytest tests/integration` | 22/23 | 1 flaky: `test_workflow_live_compile_and_run` — fail al primo run, PASS al retry. Probabile race su cloud LLM timeout |
| `pytest tests/adversarial` | 1/1 | property test ReBAC no-leakage |
| **Totale effettivo** | **117/118** | wiki claim "95 test" è stale (ora 117 effettivi) |

### API smoke (sequenza eseguita)

1. **Wipe tenant `mrd`** (eval/wipe_corpus.py): 86 retrieval_evidence + 18 query_traces + 5 chunks + 5 documents + 5 Qdrant points + 83 Neo4j nodes rimossi.
2. **Ingest** `01_internal_policy.txt` → HTTP 201, `doc_id=doc_f7e3f0f1...`, `chunks_created=1`.
3. **FGA seed** owner tuple → idempotent (già presente da run precedenti — wipe non cancella tuple FGA).
4. **Query 5 mode**:

| Mode | HTTP | Latency | Channels populated | high_trust | Answer |
|---|---|---|---|---|---|
| vector | 200 | 89s | vec=1 | false | hedge |
| lexical | 200 | 151s | lex=1 | false | hedge |
| graph | 200 | 58s | **graph=55 paths** | false | hedge |
| **hybrid** | **200** | **55s** | **vec=1 + graph=55** | **TRUE** | **"The Project Lead of Orion-7 is Maria Conti"** ✓ |
| global | 200 | 47s | community | — | structured summary |

5. **`/explain/{trace_id}`**: HTTP 200 con schema W3C PROV-O completo.
6. **`/admin/audit/traces`**: total=5 trace recenti.
7. **`/admin/permissions`**: 35 tuple, incluse le `parent_document` (fix ingest funzionante).
8. **`/admin/audit/verify-chain`**: `is_intact=false`. **Vedi punch list.**
9. **ReBAC fail-closed** con `user:eve_attacker` (non owner): answer hedge senza "Maria Conti" ✓, ma `vector.evidence_count=1` da investigare.

### Latenze elevate

Tutte le query 47-150s — la latency è dominata da Mistral cloud (1-2s/round-trip × ~10 round-trip per query in modalità multi-canale). Atteso, non bloccante.

## Frontend (PASS su build + SSR, JS interactivity da verificare manualmente)

### Build

```
> next build (Next.js 16.2.9 Turbopack)
✓ Compiled successfully in 2.6s
✓ Finished TypeScript in 2.4s    [0 errori]
✓ 12 static pages generated

Routes generated:
  /  /audit  /chat  /explain  /graph
  /health  /ingest  /login  /permissions
  /_not-found
```

### SSR smoke

```
GET /              http=200  size=21315  react-hydrated=yes
GET /login         http=200  size=23008  react-hydrated=yes
GET /chat          http=200  size=22246  react-hydrated=yes
GET /ingest        http=200  size=22260  react-hydrated=yes
GET /explain       http=200  size=22766  react-hydrated=yes
GET /audit         http=200  size=21966  react-hydrated=yes
GET /permissions   http=200  size=22295  react-hydrated=yes
GET /graph         http=200  size=22684  react-hydrated=yes
GET /health        http=200  size=21973  react-hydrated=yes
```

Note: il dev server è partito su :3001 perché :3000 era occupata da altro processo (probabile residuo).

### Cosa NON è stato testato

- Interazione JS reale (click → fetch → render dati nelle card)
- Three.js background rendering sulla home
- Switch tenant via TopBar dropdown
- 3D force graph nella pagina `/graph` (ResizeObserver fix)
- Form submission ingest reale via UI
- Dark theme + responsive su viewport diversi

Tutto questo richiede un browser umano + DevTools — fuori scope di un test CLI.

## WIP — Analisi dei 10 file non-committati

**NESSUN commit fatto** (per richiesta esplicita dell'utente). Tutti i file restano nell'albero di lavoro.

| File | Diff | Classificazione | Raccomandazione |
|---|---|---|---|
| `src/securgraph/adapters/llm/openai.py` | +53 lines | **bugfix/hardening** — retry esponenziale 5x su `RateLimitError`/`APIStatusError 429` con `Retry-After` header parsing. Necessario per la migrazione a Mistral cloud | Commit. Minor cleanup possibile (imports inline, dead `return ""` finale, mancanza unit test) |
| `src/securgraph/api/routes/admin.py` | +3 lines | **bugfix critico** — `OpenFgaClient.read()` ora richiede `body=ReadRequestTupleKey()` nella SDK aggiornata. Senza, `/admin/permissions` ritornerebbe 500 | Commit. Load-bearing per la dashboard home |
| `web/src/app/(dashboard)/page.tsx` | +85 / -42 | **feature wire** — sostituisce mock data con chiamate live a `healthApi`, `adminApi.getAuditTraces`, `adminApi.listPermissions` | Commit. Caveat: `pseudoConfidence = (0.78 + latency_ms % 22 / 100)` è una FAKE METRIC documentabile come TODO |
| `web/src/app/(dashboard)/graph/page.tsx` | +50 / -7 | **feature + ux fix** — ResizeObserver per 3D graph che riempia il card + empty-state CTA + rinomina tenant | Commit. Caveat minore: `window.location.reload()` su tenant switch (UX scadente) |
| `web/src/app/(dashboard)/{audit,chat,explain,ingest}/page.tsx` + `TopBar.tsx` | piccoli diff | **mechanical rename** `tenant_a` → `default`/`mrd`/`demo_tenant`/`test_tenant_async` (allineamento con seed reale) | Commit |
| `web/package-lock.json` | -207 lines | dependency churn da `npm install` | Commit insieme al frontend group |

**Raccomandazione raggruppamento** (proposta — l'utente decide quando/come):
- **Commit 1**: `fix(api): openfga read body + openai 429 retry` → `src/securgraph/adapters/llm/openai.py` + `src/securgraph/api/routes/admin.py`
- **Commit 2**: `feat(web): wire dashboard to live APIs + tenant rename + graph sizing` → tutti i file `web/`

## Punch list (in ordine di severità)

### BLOCKER

Nessuno. La pipeline funziona end-to-end (hybrid query produce risposta corretta con consensus high-trust).

### MAJOR

1. **Tamper-evident chain rotta** (`/admin/audit/verify-chain` ritorna `is_intact=false` su tutti i 5 query_traces e 112 retrieval_evidence). Possibili cause:
   - Secret HMAC diverso tra writer (ingest/query) e verifier (admin route)
   - Hash non scritto al momento dell'INSERT
   - Schema mismatch (es. column rename) tra writer e verifier
   - Da indagare aprendo `src/securgraph/indexing/tamper.py` + diff contro `e0f1aa805a88` migration Alembic.
2. **ReBAC fail-closed parziale**: con `user:eve_attacker` (non-owner), `vector.evidence_count=1` resta > 0 anche se l'answer non rivela il fact. Significa che vector retrieval ritorna comunque un chunk e si affida al consensus per produrre l'hedge. Sicurezza nominale OK (no leak in answer), ma la "fail-closed-prima-della-retrieval" non è stretta come la doc claims. Verifica: `src/securgraph/indexing/vector_retriever.py` chiama FGA prima o dopo la fetch?

### MINOR

3. **`type` check fail** — 21 errori mypy. Bug reale: `src/securgraph/api/routes/query.py:50,51` usa `any` (builtin) come type hint invece di `typing.Any`.
4. **`lint` fail** — 245 errori ruff. 138 auto-fixable con `ruff check --fix`. Tech debt cosmetico (line-too-long, whitespace, unused imports, import-order).
5. **Test flaky** — `tests/integration/test_workflow_live.py::test_workflow_live_compile_and_run` fallisce al primo run, passa al retry. Probabile race su cloud LLM timeout. Investigare `pytest-asyncio` event-loop semantics o aggiungere `@pytest.mark.flaky` esplicito.
6. **`.venv` root mancante** — per girare l'API ho dovuto usare `eval/.venv/bin/uvicorn`. Per consistenza con i Makefile target documentati (`make api`), rigenerare `.venv` root via `make venv`.
7. **`pseudoConfidence` fake metric** in `(dashboard)/page.tsx` — `(0.78 + latency_ms % 22 / 100.0)`. Va sostituito con un vero `confidence_score` campo restituito dall'API `/admin/audit/traces`, oppure rimosso dalla UI.
8. **`window.location.reload()` su tenant switch** in `(dashboard)/graph/page.tsx` e `(dashboard)/audit/page.tsx` — sostituire con re-fetch dei dati per non rompere stato UI.
9. **Latenza query alta** (47-150s) — Mistral cloud + multi-channel consensus. Atteso ma da documentare in user guide.
10. **`.venv/` Makefile vs eval/.venv** — Makefile assume `.venv` ma usa Mistral cloud (no Ollama locale per produzione attuale). Wiki vs config drift.

## Cross-check codice ↔ wiki

Spot-check delle claim "recently shipped" del work-tracker:

| Claim wiki | Verifica | Esito |
|---|---|---|
| M9 Web UI shipped (9 dashboard pages) | `npm run build` produce 12 route (10 user + login + 404); 9 user-facing match | ✅ |
| Cloud LLM migration (Mistral + OpenAI judge) | `.env` ha `SECURGRAPH_LLM_PROVIDER=mistral` + `RAGAS_JUDGE_PROVIDER=openai`; `adapters/llm/mistral.py` e `openai.py` esistono | ✅ |
| P0.2 baseline run completed | `eval/results/baseline-20260621T172028Z.csv` + `.md` presenti, 5 mode × 4 RAGAS metrics | ✅ |
| C3 policy-partitioned communities | `src/securgraph/community/policy_class.py` esiste, adversarial test PASS | ✅ |
| Tamper-evident audit chain (HMAC-SHA256) | Verify-chain endpoint risponde ma **`is_intact=false`** | ❌ regressione |
| ReBAC fail-closed | Test adversarial passa; smoke con non-owner non rivela facts ✓; ma `vector_evidence_count` resta > 0 | ⚠️ parziale |
| Self-healing graph EXT2 | Codice presente (workflow.py consensus hook + self_healing.py); non testato in smoke | non verificato |
| Hierarchical communities EXT3 | global mode response include community summary; community count in Neo4j = 0 in questo run (forse build offline) | parziale |

## Decisione consigliata

1. **Priorità immediata**: investigare **tamper-chain** (è uno dei contributi C1 dichiarati).
2. **Subito dopo**: capire perché vector retrieval non fa fail-closed stretto a monte (potrebbe essere by-design, ma documentare).
3. **Quick wins** prima del prossimo step grande: `ruff check --fix` (chiude 138/245 lint errors automaticamente), fix dei 2 `any` → `typing.Any` (chiude 2/21 type errors).
4. **WIP 10 file**: 2 commit raggruppati come da tabella sopra. Quando l'utente decide.
5. **`.venv` rebuild** con `make venv` per allineare ai Makefile target.

## Stato operativo lasciato

- **API uvicorn** su :8000 (task `bib3wo948`) — vivo
- **Next dev** su :3001 (task `b1dy93uv3` exit 0 ma il server è ancora vivo perché Next gira come daemon child)
- **Docker stack** healthy
- **DB stato**: 1 doc `01_internal_policy.txt` ingestato + 5 trace di query (vector / lexical / graph / hybrid / global) + tuple FGA persistite. Pulibile con `eval/wipe_corpus.py --tenant mrd --yes`.
