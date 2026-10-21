# Web UI — Implementation Plan

**Milestone:** WEB-UI (M9)  
**Stack:** Next.js 15 (App Router) + shadcn/ui + Tailwind CSS + Three.js + React Flow + 3d-force-graph  
**Location:** `web/`  
**API Backend:** existing FastAPI at `localhost:8000`

---

## Overview

Build the complete Web UI for SecurGraph RAG in 4 incremental phases:
1. **Foundation** — scaffold, layout, auth, health
2. **Core** — chat/query, document ingestion
3. **Provenance** — explain viewer, audit log
4. **Admin** — permissions management, knowledge graph explorer

Each phase is independently deployable and testable.

---

## Phase 1: Foundation

### Task 1.1 — Next.js Project Init
- Run `npx create-next-app@latest web/ --ts --tailwind --eslint --app --src-dir --import-alias "@/*"` (non-interactive)
- Configure `next.config.js` for API proxy to `localhost:8000`
- Update `.gitignore` for `web/node_modules`, `web/.next`
- Add `web-dev` target to root `Makefile`

### Task 1.2 — shadcn/ui Setup + Dark Theme
- Initialize shadcn/ui: `npx shadcn@latest init`
- Install core components: button, input, card, dialog, dropdown-menu, table, badge, separator, sheet, tabs, tooltip, avatar, scroll-area
- Configure Tailwind with the dark premium color palette from design spec
- Set up CSS variables matching the presentation design tokens
- Configure fonts: Inter (sans) + JetBrains Mono (mono)

### Task 1.3 — Three.js Background Component
- Create `components/layout/ThreeBackground.tsx`
- Port particle network from presentation HTML to React/Three.js
  - Use `useRef` for canvas, `useEffect` for animation loop
  - Props: `particleCount`, `opacity`, `interactive`
  - Performance: pause animation when `document.hidden`
  - Respect `prefers-reduced-motion`
- Low-opacity variant for app background vs full-opacity for login

### Task 1.4 — App Layout + Sidebar Navigation
- Create root `app/layout.tsx` with ThreeBackground behind content
- Create `components/layout/Sidebar.tsx`:
  - Collapsible sidebar (icon-only when collapsed)
  - Navigation items: Dashboard, Chat, Ingest, Explain, Permissions, Health, Audit, Graph
  - Lucide icons for each
  - Active page highlight
  - User avatar + tenant info at bottom
  - Glassmorphism styling
- Create `components/layout/TopBar.tsx`:
  - Breadcrumb
  - User dropdown (logout, settings)
  - Tenant selector dropdown

### Task 1.5 — API Client Layer
- Create `lib/api.ts`: typed fetch wrappers
  - Base URL configuration (env var or proxy)
  - JWT token attachment via Authorization header
  - Error handling (401 → redirect to login)
  - Typed response interfaces
- Create `lib/types.ts`: TypeScript interfaces matching all Pydantic domain models
  - QueryRequest, QueryResponse, ConfidenceBlock, etc.
  - IngestResponse, IngestionJobStatusResponse
  - ExplainReport, HealthResponse
- Create `lib/auth.ts`: session management
  - Store JWT in httpOnly cookie or localStorage
  - `useAuth()` hook returning user/tenant/logout

### Task 1.6 — Backend Auth Endpoint
- Create `src/securgraph/api/routes/auth.py`:
  - `POST /auth/login` — simple username/password validation
  - `GET /auth/me` — return user from JWT
  - Users table: `auth_users` with id, username, password_hash, tenant_id, role
  - Alembic migration for `auth_users` table
  - Seed 3 demo users: admin/analyst/viewer per tenant
- Register auth router in `main.py`
- JWT signing with `python-jose` (HS256, secret from .env)

### Task 1.7 — Login Page
- Create `app/login/page.tsx`
- Full-screen ThreeBackground (high particle count)
- Centered glassmorphism card with:
  - SecurGraph logo + gradient text
  - Username + password inputs (shadcn)
  - Submit button with loading state
  - Error message display
- On success: store token, redirect to `/`

### Task 1.8 — Health Dashboard Page
- Create `app/health/page.tsx`
- Call `GET /health` endpoint
- Render 6 `BackendStatusCard` components:
  - Postgres, Qdrant, Neo4j, OpenFGA, Ollama, Redis
  - Status badge (ok = green, degraded = amber, unavailable = red)
  - Latency display
  - Detail text
- Auto-refresh every 10 seconds
- Service info bar: version, env, service name

### Task 1.9 — Dashboard Landing Page
- Create `app/page.tsx` (main dashboard after login)
- 4 summary stat cards: total docs, total queries, avg confidence, overall health
- Recent queries list (last 10) with clickable trace_ids
- Quick action buttons: "New Query" → /chat, "Upload Document" → /ingest
- Glassmorphism cards with gradient borders

**Phase 1 Acceptance Criteria:**
- `npm run dev` starts the web app
- Login → Dashboard → Health flow works end-to-end
- Three.js particle background renders on login + as app background
- Sidebar navigation between all pages (empty placeholder pages)

---

## Phase 2: Core Features

### Task 2.1 — Chat/Query Interface
- Create `app/chat/page.tsx` with split layout
- **Left panel — Message history:**
  - `components/chat/MessageBubble.tsx` — user vs system styles
  - `components/chat/AnswerCard.tsx`:
    - Markdown-rendered answer text
    - ConfidenceBadge component (green/amber/red based on high_trust + agreement_score)
    - Agreement score + entity overlap progress bars
    - Expandable citations accordion
    - Channel diagnostics: vector/lexical/graph evidence counts
    - Link to `/explain/{trace_id}`
    - Warnings banner
  - Scrollable message list with auto-scroll to latest
- **Right panel — Query Options:**
  - Retrieval mode selector (radio group): auto/vector/lexical/graph/hybrid
  - Query type: standard/global toggle
  - Max graph depth: slider 1-4
  - Top K: slider 1-100
  - Consensus threshold: slider 0.0-1.0
  - Collapsible panel
- **Input bar:**
  - Text input + send button
  - Enter to send, Shift+Enter for newline
  - Loading state during query execution

### Task 2.2 — Document Ingestion Page
- Create `app/ingest/page.tsx`
- **Upload zone:**
  - `components/ingest/UploadDropzone.tsx` — drag & drop area
  - Accepts PDF, DOCX, MD files
  - File preview (name, size, type)
  - 50MB limit with client-side validation
- **Metadata form:**
  - Tenant ID (pre-filled, read-only from session)
  - Owner ID (pre-filled from session)
  - Document Type dropdown
  - Classification dropdown (public → top_secret)
  - Policy tags: chip input
  - Title override: optional text input
- **Submit button** — posts multipart/form-data to `/ingest`
- **Active Jobs panel:**
  - `components/ingest/JobStatusTracker.tsx`
  - Table of recent ingestion jobs
  - Status polling every 3 seconds for queued/running jobs
  - Status pills: queued (blue), running (amber), completed (green), failed (red)
  - Error detail expandable for failed jobs

**Phase 2 Acceptance Criteria:**
- User can type a query in chat and see the full response with confidence and citations
- User can upload a document and track ingestion status
- All API errors are gracefully handled with user-friendly messages

---

## Phase 3: Provenance + Audit

### Task 3.1 — Explain/Provenance Page
- Create `app/explain/page.tsx`
- **Search:** trace_id input or dropdown of recent query traces
- **React Flow Lineage Graph:**
  - `components/explain/ProvLineageGraph.tsx`
  - Custom node types: Agent (person icon), Activity (gear icon), Entity (doc icon)
  - Edge types: wasGeneratedBy, wasDerivedFrom, wasUsedIn
  - Color coding: allowed = green nodes/edges, denied = red with lock icon
  - Auto-layout with dagre algorithm
  - Zoom + pan controls
- **Evidence Table:**
  - `components/explain/EvidenceTable.tsx`
  - Columns: evidence_id, retriever, object_id, rank, score, status
  - Redacted rows: grayed out with lock icon, "CONFIDENTIAL"
- **Policy Summary:**
  - Donut chart: allowed vs denied counts
  - Summary stats
- **Timing Info:** latency_ms, timestamps

### Task 3.2 — Backend Audit Endpoint
- Create `src/securgraph/api/routes/admin.py`:
  - `GET /admin/audit/traces` — paginated list of QueryTraceRow for tenant
    - Query params: page, per_page, user_id filter, date_from/to, high_trust_only
  - `POST /admin/audit/verify-chain` — runs `validate_chain()` for a table + tenant
    - Returns chain_valid, chain_length, first_hash, last_hash
- Register admin router in `main.py`

### Task 3.3 — Audit Log Page
- Create `app/audit/page.tsx`
- **Query Trace Table:**
  - `components/audit/AuditLogTable.tsx`
  - Columns: trace_id, user_id, query (truncated), latency, timestamp, trust badge
  - Click row → navigate to `/explain/{trace_id}`
  - Filters: user_id, date range, high_trust only
  - Pagination controls
- **Tamper Verification Panel:**
  - `components/audit/TamperVerifier.tsx`
  - Table selector dropdown (policy_decisions, query_traces, evidence_chunks)
  - "Verify Chain" button
  - Result display: ✅ Chain Valid / ❌ Chain Tampered
  - Chain stats: length, first/last hash

**Phase 3 Acceptance Criteria:**
- Explain page renders interactive PROV-O lineage graph for any trace_id
- Audit log displays paginated query history with filters
- Tamper verification runs and reports chain integrity

---

## Phase 4: Admin + Graph Explorer

### Task 4.1 — Backend Permissions + Graph Endpoints
- Add to `src/securgraph/api/routes/admin.py`:
  - `GET /admin/permissions` — lists FGA tuples for a tenant via `_fga_client.read()`
  - `POST /admin/permissions` — writes/deletes FGA tuples
    - Body: `{ action: "grant"|"revoke", user_id, object_type, object_id, relation }`
  - `GET /admin/graph/nodes` — exports Neo4j entities + relationships for viz
    - Query params: tenant_id, limit, entity_type, community_id
    - Returns nodes (id, label, type, community_id) + edges (source, target, type, label)
  - `GET /admin/graph/communities` — exports community hierarchy

### Task 4.2 — Permission Management Page
- Create `app/permissions/page.tsx`
- **User-Document Matrix:**
  - `components/permissions/PermissionMatrix.tsx`
  - Grid: rows = users, cols = documents
  - Cells: access level badge or empty
  - Click cell → dialog to grant/revoke
- **Document Detail Panel:**
  - Slide-out sheet with document metadata
  - Current viewers list
  - Add/remove viewer form
- **Bulk Actions:**
  - Grant viewer to user across all docs in a classification
  - Revoke all access for a user

### Task 4.3 — Knowledge Graph Explorer Page
- Create `app/graph/page.tsx`
- **3D Force Graph:**
  - `components/graph/ForceGraph3D.tsx`
  - Full-viewport 3d-force-graph instance
  - Nodes: colored by entity type (person, org, concept, policy, event)
  - Edges: with labels, directed
  - Communities: color clusters matching Louvain partitions
  - Interactive: click node → detail panel, zoom/rotate/pan
  - Node size based on connection count
- **Sidebar Filters:**
  - Entity type checkboxes
  - Community filter dropdown
  - Relationship type filter
  - Search by entity name
- **Node Detail Panel (slide-out Sheet):**
  - Entity name, type, properties
  - Connected edges with targets
  - Source chunks mentioning this entity
  - Community and policy_class_key

**Phase 4 Acceptance Criteria:**
- Permission matrix shows user-document access grid
- Graph explorer renders 3D force-directed graph with entity data from Neo4j
- Node click reveals detail panel
- Community coloring visible

---

## Backend Changes Summary

| File | Change | Phase |
|------|--------|-------|
| `src/securgraph/api/routes/auth.py` | **[NEW]** Login, JWT, user management | Phase 1 |
| `src/securgraph/api/routes/admin.py` | **[NEW]** Audit, permissions, graph export endpoints | Phase 3-4 |
| `src/securgraph/api/main.py` | **[MODIFY]** Mount auth + admin routers | Phase 1 |
| `alembic/versions/xxx_auth_users.py` | **[NEW]** Auth users migration | Phase 1 |
| `src/securgraph/config.py` | **[MODIFY]** Add JWT_SECRET, AUTH_ENABLED settings | Phase 1 |

---

## Verification Plan

### Per Phase
- `npm run build` (Next.js production build) must succeed
- `npm run lint` must pass
- Manual testing of each page against running FastAPI backend

### End-to-End Flow
1. Login → Dashboard shows stats
2. Upload a document → track job completion
3. Query the system → see answer with confidence
4. Click trace_id → view provenance lineage graph
5. Check audit log → verify tamper-evident chain
6. Manage permissions → grant/revoke access
7. Explore knowledge graph in 3D
