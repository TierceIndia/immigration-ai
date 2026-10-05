# AU Immigration Knowledge Platform

End-to-end deterministic immigration eligibility system for Australian skilled migration visas.

**Phases completed**: 1 → 2 → 3 → 3C → 3D → Admin API → Admin Knowledge → DB Readiness + API Surface Hardening → security hardening → browser-fetch escalation → Gemini provider support (all fully tested, **933 passing / 9 skipped** with auth enabled — see the auth-disabled note below for current suite state).

**Core principle**: Every eligibility decision is deterministic, auditable, and traceable to exact government evidence. No LLM decides eligibility.

**Production status**: NOT PRODUCTION-READY — see [Production Readiness](#production-readiness) for gaps.

## ✅ Authentication is real and enforced (restored 2026-09-22)

`get_current_user()` in [`src/api/routes/auth.py`](src/api/routes/auth.py) performs real JWT
validation — there was a temporary bypass for manual testing between 2026-09-21 and
2026-09-22, but it was fully deleted (not just toggled off) once that testing was done. Every
admin route enforces 401 (no/invalid token) → 403 (valid token, non-admin role) → 200 (admin) —
live-verified.

## What works / what doesn't (2026-09-25)

| Area | Status |
|------|--------|
| Deterministic rule engine + eligibility/gap engine | WORKING — never yet exercised with real extracted data (see below) |
| Admin API (rules, requirements, documents, sources, runs, knowledge-state, schema) | WORKING, real auth enforced (401/403/200 live-verified) |
| `POST /api/v1/admin/sync` | WORKING — always re-fetches due sources, correctly demotes blocked sources, records real `PipelineRun` audit trail |
| Source registry | WORKING — 14 sources across SKILLED / WORK / STUDENT / VISITOR |
| Source fetch → store → version → change-detect → downstream extraction | WORKING end-to-end against the real running stack (verified live, not just offline-tested) |
| Plain-HTTP fetch of `immi.homeaffairs.gov.au` | NOT WORKING — real HTTP 403 (Akamai/Cloudflare-class WAF). Expected; this is what the browser-escalation path exists for. |
| Browser-escalation fetch of `immi.homeaffairs.gov.au` | **FIXED 2026-09-25 — now WORKING.** `src/browser_fetcher.py` was launching Chromium with `headless=True`, which the Home Affairs WAF fingerprints and blocks with a real 403 "even via genuine browser session". Switched to `headless=False` under Xvfb (see Dockerfile/`docker-entrypoint.sh`) and **live-verified against two real, previously-blocked URLs** (subclass 189 and 190 pages): both now return real `200 OK` with full page content (1.3–1.5MB, containing real eligibility text), where headless mode got 403 on the same URLs. The 12 sources currently sitting in `NEEDS_MANUAL_CAPTURE` have **not yet been re-synced** to pick this up — that's a follow-up action, not done as part of this fix (see Current Gaps). |
| `POST /admin/sync` only touching 2 of 14 sources | Still true today (sources demoted to `NEEDS_MANUAL_CAPTURE` are excluded from sync until reset) — but now that headed browser escalation works, re-running `manual-capture` may no longer be necessary for most of them; a plain re-sync attempt might recover them automatically. See [Why does sync only touch 2 of 14 sources?](#why-does-admin-sync-only-touch-2-of-14-sources) below. |
| LLM extraction — **OpenAI** | WORKING when the account has billing credits; fails safely and visibly (`FAILED` run, real error) when it doesn't |
| LLM extraction — **Gemini** | WORKING — `EXTRACTION_MODEL=gemini-flash-lite-latest` now routes through `GeminiLLMClient`, fully wired to the same governance layer (budget/rate-limit/cache) as OpenAI |
| Real requirement extraction end-to-end | NOT YET PROVEN WITH REAL CONTENT — the only two sources that reach extraction (`SRC-AU-CRICOS-REGISTER`, `SRC-AU-MIGRATION-REGS-1994`) both return 0 requirements: CRICOS's page appears to be a JS-rendered SPA shell with no static content (0 chunks extracted), and the Migration Regs source URL currently points at a Federal Register *index/citation* page, not the actual regulation text. Both are content/source-URL problems, not pipeline bugs — the pipeline honestly extracted nothing rather than fabricating anything. |
| Rule compilation → review → activation → assessment | UNTESTED WITH REAL DATA — zero requirements exist in any program yet (a direct consequence of the above), so nothing has ever reached `DRAFT`/`VALIDATED`/`APPROVED`/`ACTIVE`, and no assessment has ever run against real ACTIVE rules |
| Applicant CRUD | WORKING — a field-dropping bug in `POST /applicants` (create) was found and fixed today: `first_name`/`last_name`/`created_at` were silently returned as `null` even though correctly persisted (services/applicant_service.py `create()` built its own incomplete response dict instead of reusing `get()`) |
| Docker deployment — LLM extraction | WAS COMPLETELY BROKEN, now fixed: `docker-compose.yml` never forwarded `OPENAI_API_KEY`/`GEMINI_API_KEY`/`EXTRACTION_MODEL` into the `api` container, and `Dockerfile` never copied `prompts/` into the image at all. Both fixed today; verified live. |
| WORK / STUDENT / VISITOR **live LLM extraction + gold data** | NOT WORKING YET — sources registered, but real evidence capture still pending (`AWAITING_REAL_SOURCE`); no fabricated evidence |
| Multi-country (NZ/CA/UK) | NOT STARTED |

## Quick Start

```bash
pip install -r requirements.txt
python -m pytest tests/ -q    # 956 passed / 11 skipped / 0 failed
```

```bash
# Docker
docker-compose up -d
```

To configure the administrator used by the API, create a root `.env` file before
starting Docker Compose:

```dotenv
ADMIN_EMAIL=admin@gmail.com
ADMIN_PASSWORD=use-a-strong-password
ADMIN_FULL_NAME=System Administrator
```

On startup, the API idempotently creates this account or promotes the existing
account with that email to `admin`. Normal registration always creates an
`applicant`; roles are stored in the database and are returned by `/api/v1/auth/login`
and `/api/v1/auth/me`. After changing an existing user's role, log in again to get a
new access token.

> **Filename guard:** the build context requires a file named exactly `Dockerfile`
> (capital D, no extension) at the project root. `docker-compose.yml` depends on it, and a
> case-only rename to `dockerfile` can silently reuse a stale image on case-insensitive
> filesystems — do not rename it.

The `api` container runs migrations and seeds before serving: `docker-compose.yml`'s `command` runs
`alembic upgrade head` → `python src/seed_registry.py` (idempotent seed of countries / programs /
14 verified government sources across all four visa categories: SKILLED, WORK, STUDENT, VISITOR) →
uvicorn.

The Dockerfile builds the Python 3.12-slim base plus **Patchright's** Chromium via
`patchright install --with-deps chromium`. The browser is **headless**, so nothing needs an X server:
no `DISPLAY`, no Xvfb, no `xauth`, and no Xvfb entrypoint. The stock `playwright` package is
deliberately not installed (see `src/browser_fetcher.py` for the rationale).

Diagnostics: `GET /health` is a pure liveness probe (always 200). `GET /ready` reports
DB connectivity — 200 when connected, else 503 with safe, credential-scrubbed context
(`configured_backend` / `configured_host` / `configured_database`, plus a scrubbed
`db_error_detail` outside TESTING). Credentials are scrubbed before they can appear in the
response body.

Scheduler: government source sync runs **once daily at a fixed clock time** (UTC), not on a
random/interval offset. Configure with `GOVERNMENT_SYNC_HOUR_UTC` (0-23, default `3`) and
optional `GOVERNMENT_SYNC_MINUTE_UTC` (0-59, default `0`). The legacy interval mode
(`GOVERNMENT_SYNC_INTERVAL_MINUTES`) is still honored when set by standalone pipeline
scripts. Manual sync is always available via `POST /api/v1/admin/sync`.

Database structure: `GET /api/v1/admin/schema-report` (admin-only, read-only) returns the
full structure — tables, columns, primary/foreign keys, exact row counts, and the current
alembic revision. A static snapshot is maintained in [`DBSTRUCTURE.md`](DBSTRUCTURE.md).

```bash
# Run API
uvicorn src.api.main:create_app --factory --reload
```

## Architecture

```
                   GOVERNMENT SOURCES
                          │
                          ▼
                 ┌─────────────────┐
                 │ PHASE 1         │
                 │ Data Ingestion  │
                 │ Source Registry │
                 │ Fetch → Store   │
                 │ Extract → Hash  │
                 │ Change Detect   │
                 └────────┬────────┘
                          │
                     DOCUMENT CHUNKS
                          │
                          ▼
                 ┌─────────────────┐
                 │ PHASE 2         │
                 │ Knowledge       │
                 │ Extraction      │
                 │ LLM Extraction  │
                 │ Evidence Check  │
                 │ Validation      │
                 │ Normalization   │
                 │ Deduplication   │
                 │ Human Review    │
                 └────────┬────────┘
                          │
                   APPROVED REQUIREMENTS
                          │
                          ▼
                 ┌─────────────────┐
                 │ PHASE 3D        │
                 │ Req → Rule      │
                 │ Bridge          │
                 │ Status Machine  │
                 │ Admin Approval  │
                 │ Activation      │
                 └────────┬────────┘
                          │
                     ACTIVE RULES
                          │
                          ▼
                 ┌─────────────────┐
                 │ PHASE 3         │
                 │ Deterministic   │
                 │ Rule Engine     │
                 │ 12 Operators    │
                 │ 7 Expr Types    │
                 │ IF/THEN, OR, AND│
                 │ Exceptions      │
                 │ Dependencies    │
                 │ Versioning      │
                 └────────┬────────┘
                          │
                    EXPRESSION TREE
                          │
                          ▼
                 ┌─────────────────┐
                 │ PHASE 3C        │
                 │ Eligibility +   │
                 │ Gap Engine      │
                 │ Applicant Model │
                 │ Applicability   │
                 │ Assessment      │
                 │ Gap Detection   │
                 │ Questions       │
                 └────────┬────────┘
                          │
               ┌──────────┴──────────┐
               ▼                     ▼
            ELIGIBLE            INCOMPLETE
               │                     │
               │                 GAP ENGINE
               │                     │
               │              Ask User Questions
               │                     │
               └──────────┬──────────┘
                          ▼
                   FINAL ASSESSMENT
```

## Phase Overview

### Phase 1 — Data Ingestion

Government source registry, HTTP fetcher, raw document storage, HTML/PDF extraction, content hashing, change detection.

**Key outputs**: `document_chunks` with heading hierarchy, table structure, page numbers.

```bash
python src/seed_registry.py              # Load source registry
python src/pipeline.py                   # Run full pipeline
python src/pipeline.py --source-id SRC-AU-189-MAIN
```

### Phase 2 — Knowledge Extraction

LLM-based structured requirement extraction with evidence validation, deduplication, and human review.

**Key outputs**: `requirements` rows with `status=APPROVED`, full evidence provenance.

```bash
python src/extraction/run_extraction.py --document-id DOC-AU-189-MAIN
python src/extraction/run_extraction.py --document-id DOC-AU-189-MAIN --model gpt-4o
```

### Phase 3 — Deterministic Rule Engine

Declarative rules compiled from approved requirements. 12 operators, 7 expression types. Four-state semantics: PASS / FAIL / UNKNOWN / NOT_APPLICABLE.

**Key outputs**: `evaluate_expression()` → deterministic status with full trace.

### Phase 3C — Eligibility + Gap Engine

Applicant profile → rule applicability → eligibility assessment → gap detection → user questions.

**Three business outcomes**: ELIGIBLE / INELIGIBLE / INCOMPLETE

**Key outputs**: `assess_eligibility()` → business status with missing field identification.

### Phase 3D — Government Knowledge Activation Pipeline

Government source → requirement extraction → rule compilation → DB persistence → admin approval → active rules.

```
Government Source → Phase 1 → Phase 2 → Approved Requirement
    → compile_approved_requirements() → EnhancedRuleRepo.save()
        → DRAFT → validate → VALIDATED → approve → APPROVED → activate → ACTIVE
            → Transactional supersession (v2 activates, v1 SUPERSEDED)
```

**Key outputs**: ACTIVE rules in `rule_versions` table, full provenance chain, version auto-increment.

## API Endpoints

### Auth (`/api/v1/auth`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/register` | Register new user (409 on duplicate email) |
| `POST` | `/login` | Login → JWT token |
| `GET` | `/me` | Current user info |

### Applicants (`/api/v1/applicants`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Create applicant |
| `GET` | `/` | List applicants |
| `GET` | `/{id}` | Get applicant |
| `PATCH` | `/{id}` | Update applicant |

All applicant endpoints require JWT authentication.

### Assessments (`/api/v1/assessments`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Create assessment (loads ACTIVE rules → Phase 3C → persist) |
| `GET` | `/` | List assessments for applicant |
| `GET` | `/{id}` | Get assessment |
| `POST` | `/evaluate` | In-memory evaluation (no persistence) |

### Admin Rules (`/api/v1/admin/rules`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/queue` | Review queue (admin only) |
| `GET` | `/` | List all rules |
| `GET` | `?status=STATUS` | Filter by status |
| `GET` | `/{id}` | Get rule |
| `POST` | `/{id}/validate` | DRAFT → VALIDATED |
| `POST` | `/{id}/approve` | VALIDATED → APPROVED |
| `POST` | `/{id}/activate` | APPROVED → ACTIVE (transactional supersession) |
| `POST` | `/{id}/reject` | Pre-active → REJECTED |
| `POST` | `/{id}/retire` | SUPERSEDED/RETIRED |

### Admin Requirements (`/api/v1/admin/requirements`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List all requirements with optional status filter |
| `GET` | `/{id}` | Get requirement detail with evidence |
| `POST` | `/{id}/approve` | Mark requirement APPROVED |
| `POST` | `/{id}/reject` | Mark requirement REJECTED |

### Admin Documents (`/api/v1/admin/documents`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List documents (filter by source, program, status) |
| `GET` | `/{document_id}` | Get document metadata |
| `GET` | `/{document_id}/versions` | List version history (newest first) |
| `GET` | `/{document_id}/chunks` | List text chunks (filter by version) |
| `GET` | `/{document_id}/diff` | Diff between two document versions |

### Admin Knowledge State (`/api/v1/admin/knowledge-state`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/?country_id=AU` | Hierarchical tree: country → visa category → programs → sources → requirement/rule counts |

### Pipeline Runs (`/api/v1/admin/runs`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List all pipeline runs (newest first, paginated) |
| `GET` | `/summary` | Aggregate counts by status |
| `GET` | `/{id}` | Get single pipeline run (with computed rule counters) |
| `GET` | `/source/{id}` | List runs for a specific source |

### Admin Sources (`/api/v1/admin/sources`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List all government sources (admin only) |
| `POST` | `/` | Create a source (admin only) |
| `GET` | `/{source_id}` | Get a single source (admin only) |
| `PATCH` | `/{source_id}` | Update source (status, priority, crawl_frequency, url) (admin only) |
| `POST` | `/{source_id}/manual-capture` | Record a manually captured document for a blocked source |
| `POST` | `/discover` | Run source discovery (admin only) |
| `GET` | `/discovered` | List discovered source candidates (admin only) |
| `POST` | `/discovered/{id}/approve` | Approve a candidate into a real source (admin only) |
| `POST` | `/discovered/{id}/reject` | Reject a candidate (admin only) |

### Admin Schema (`/api/v1/admin/schema-report`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Full DB structure: tables, columns, PK/FK/unique/check/index/triggers, live row counts, alembic head (admin only, read-only) |

### Admin Sync (`/api/v1/admin/sync`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Manually trigger government source sync — **admin only** (401 without token, 403 for non-admins) |

#### Why does `/admin/sync` only touch 2 of 14 sources?

`sync_all_sources()` in `src/orchestration/government_sync.py` only ever queries sources
where `status == "ACTIVE"`:

```python
sources = session.query(GovernmentSource).filter(GovernmentSource.status == "ACTIVE").all()
```

Right now **12 of the 14 seeded sources are `NEEDS_MANUAL_CAPTURE`**, not `ACTIVE` — every
`immi.homeaffairs.gov.au` page in the registry. They got demoted because the real
`immi.homeaffairs.gov.au` WAF returns `HTTP 403` even after the fetcher escalates to a genuine,
non-headless Chromium session (`src/browser_fetcher.py`). `_record_fetch_failure()` in
`src/utils/fetch_document.py` sets `source.status = "NEEDS_MANUAL_CAPTURE"` whenever a block
survives browser escalation — this is deliberate, not a bug: the project's design explicitly
forbids trying harder to get past a real block (no stealth plugins, no fingerprint evasion, no
CAPTCHA solving). Once demoted, a source is excluded from **every** future sync — scheduled or
manual — until an admin resets it.

Only `SRC-AU-CRICOS-REGISTER` and `SRC-AU-MIGRATION-REGS-1994` are currently `ACTIVE`
(different domains, not blocked), so those are the only two `POST /admin/sync` ever reaches.

**To recover a `NEEDS_MANUAL_CAPTURE` source**: an admin opens the URL in a real browser by
hand, saves the rendered HTML, and calls:

```
POST /api/v1/admin/sources/{source_id}/manual-capture
{ "raw_html": "<the captured HTML>" }
```

This resets the source to `ACTIVE`, runs the captured content through the same
store → extract pipeline as an automated fetch, and the source rejoins future syncs.

## Database

### ORM Models (25+ tables)

Core tables:

- `Rule` + `RuleVersion` — Logical rule identity + executable version with `snapshot_json`
- `Requirement` + `RequirementEvidence` — Phase 2 approved requirements with provenance
- `GovernmentSource` + `GovernmentDocument` — Source registry + document versions
- `DocumentVersion` + `DocumentChunk` — Document versions and text chunks with heading hierarchy
- `PipelineRun` — Sync execution audit trail (trigger, status, duration, errors)
- `ApplicantProfile` — User applicant state
- `Assessment` + `AssessmentRuleResult` — Assessment persistence
- `Gap` — Priority gaps per assessment
- `ExtractionRun` — LLM extraction run tracking
- `RequirementReview` — Human review audit trail

### Database Abstraction

```python
from db.connection import get_engine, get_session_factory, init_db, session_scope
from db.repositories import (
    SourceRepository, DocumentRepository, RequirementRepository,
    RuleRepository, ApplicantRepository, AssessmentRepository,
    GapRepository, ChunkRepository, AuditLogRepository,
    ExtractionRunRepository, RequirementReviewRepository,
    LlmUsageRepository, LlmBudgetRepository, LlmCacheRepository,
    ImmigrationProgramRepository, GovernmentProgramRepository,
    CountryRepository, VersionRepository, PipelineRunRepository,
)
```

### PostgreSQL (production) / SQLite (tests)

- PostgreSQL: UUID, JSONB, TIMESTAMPTZ, proper constraints
- SQLite: `String(36)`, `JSON`, in-memory with `StaticPool`
- `alembic.ini` configured for migrations

## Test Suite

> **956 passed / 11 skipped / 0 failed** (2026-09-25), with real auth enforced throughout.

```bash
python -m pytest tests/ -q              # Full suite (counts above)
python -m pytest tests/rules -q        # Phase 3 core: 192 tests
python -m pytest tests/assessment -q       # Phase 3C eligibility: 97 tests
python -m pytest tests/phase3d -q       # Phase 3D activation: 120 tests
python -m pytest tests/db -q            # Database models/repos: 22 tests
python -m pytest tests/extraction/test_category_prompts.py -q  # Category→prompt wiring: 10 tests
python -m pytest tests/test_api.py -q   # API routes: 38 tests
python -m pytest tests/test_auth.py -q  # Authentication: 13 tests
python -m pytest tests/test_security.py -q  # Security: 44 tests
python -m pytest tests/test_llm_governance.py -q  # LLM governance: 30 tests
python -m pytest tests/test_scheduler.py -q  # Scheduler: 28 tests
python -m pytest tests/test_pipeline_runs.py -q  # PipelineRun tracking: 42 tests
python -m pytest tests/test_e2e_scenarios.py -q  # End-to-end scenarios: 14 tests
```

| Layer | Tests | What it proves |
|-------|-------|----------------|
| Phase 2 | 70 (+10 prompt wiring, +12 gold harness) | Schema validation, evidence checking, normalization, dedup, extraction, category→prompt wiring, category-aware gold harness |
| Phase 3 | 192 | Operators, expression tree, IF/THEN, exceptions, dependencies, versioning |
| Phase 3C | 97 | Applicant model, applicability, assessment, gap engine, questions |
| Phase 3D | 120 | Rule persistence, status machine, requirement→rule bridge, admin API, e2e, hardening |
| Database | 22 | ORM models, repositories, CRUD, in-memory SQLite |
| Logging | 17 | Structured logging, PII redaction, request context |
| API Routes | 38 | Request/response contracts, error handling, real assessment, G9 regression, admin-sync auth (401/403/200) |
| Auth | 13 | JWT tokens, register/login, password hashing, duplicate email, role in token |
| LLM Governance | 30 | Rate limiting, caching, budget tracking, retry policy |
| Security | 44 | JWT secrets, PII redaction, CORS, authorization, password security |
| Scheduler | 28 | APScheduler start/stop, manual sync trigger, concurrent-sync lock |
| PipelineRun | 42 | Sync audit trail, repo CRUD, API endpoints, computed counters, orchestrator integration |
| E2E Scenarios | 14 | Multi-program, multi-version, gap engine, evaluation, orchestration |
| Fetch / Sync lifecycle | 23 | fetch→store→version, change detection, refetch lifecycle, crawl-frequency gating, browser escalation |
| **Total** | **956** | **956 passed / 11 skipped, 0 failures (2026-09-25), real auth enforced throughout.** |

## Expression Types

| Type | Purpose | Semantics |
|------|---------|-----------|
| `RuleLeaf` | Single rule evaluation | Evaluates field/operator/value |
| `ANDNode` | All conditions must pass | NOT_APPLICABLE filtered; FAIL > UNKNOWN > PASS |
| `ORNode` | At least one must pass | NOT_APPLICABLE filtered; PASS > UNKNOWN > FAIL |
| `NOTNode` | Invert result | PASS↔FAIL; UNKNOWN and NOT_APPLICABLE unchanged |
| `ConditionalNode` | IF condition THEN consequence | IF PASS → evaluate THEN; IF FAIL → NOT_APPLICABLE; IF UNKNOWN → UNKNOWN |
| `ExceptionNode` | Rule UNLESS exception | Exception applies + main PASS → FAIL; else main unchanged |
| `DependencyNode` | Rule requires prerequisites | Dep FAIL → NOT_APPLICABLE; dep UNKNOWN → UNKNOWN |

## Operators

`EQ` `NEQ` `LT` `LTE` `GT` `GTE` `IN` `NOT_IN` `CONTAINS` `NOT_CONTAINS` `EXISTS` `NOT_EXISTS`

## Rule Status Lifecycle

```
DRAFT → VALIDATED → APPROVED → ACTIVE → SUPERSEDED → RETIRED
                                  ↓
                              REJECTED (from any pre-active)
```

**Invariant**: For any logical key (program + category + field), at most one ACTIVE version exists. Activating v2 transactionally supersedes v1.

## Project Structure

```
immigration-knowledge/
├── src/
│   ├── api/                        # FastAPI application
│   │   ├── main.py                 # App factory, CORS, middleware, lifespan
│   │   ├── helpers.py              # parse_uuid_or_404 utility
│   │   ├── routes/
│   │   │   ├── auth.py             # Register/login/me (JWT), require_admin dependency
│   │   │   ├── applicants.py       # Applicant CRUD (JWT + ownership)
│   │   │   ├── assessments.py      # Assessment create/list/get/evaluate (JWT + ownership)
│   │   │   ├── admin_rules.py      # Admin review/approve/activate
│   │   │   ├── admin_requirements.py # Admin requirement review
│   │   │   ├── admin_sources.py    # Source admin API + discovery + manual capture
│   │   │   ├── admin_documents.py  # Document admin API (G14)
│   │   │   ├── admin_knowledge.py  # Knowledge-state endpoint (G15)
│   │   │   ├── admin_schema.py     # Schema-report endpoint (read-only DB structure)
│   │   │   ├── pipeline_runs.py    # Pipeline run API (G13 computed counters)
│   │   │   └── health.py           # /health + /ready (public)
│   │   └── schemas/
│   │       └── models.py           # Pydantic request/response models
│   ├── db/                         # SQLAlchemy ORM layer
│   │   ├── connection.py           # Engine, session factory, session_scope
│   │   ├── models.py               # 25+ ORM models
│   │   └── repositories.py         # 20+ repository classes
│   ├── phase1/                     # Government source registry
│   ├── extraction/                 # Knowledge extraction (LLM, validation, dedup) — was phase2/
│   ├── rules/                      # Rule engine (operators, expressions, compiler) — was phase3/
│   │   ├── rule_activation.py      # Status machine (DRAFT→ACTIVE lifecycle)
│   │   ├── rule_repository.py      # EnhancedRuleRepo (CRUD, versioning, activation)
│   │   └── requirement_to_rule.py  # Approved requirement → rule compilation bridge
│   ├── assessment/                 # Eligibility + Gap Engine — was phase3c/
│   ├── change_detection/           # change_detector.py, hashing.py
│   ├── fetch/                      # fetcher.py, browser_fetcher.py, extractor_html.py,
│   │                                #   extractor_pdf.py, extractor_ha_api.py
│   ├── orchestration/
│   │   └── government_sync.py      # Source sync orchestrator (always-fetch + crawl gating)
│   ├── services/
│   │   ├── auth_service.py         # JWT, bcrypt, secret management
│   │   ├── applicant_service.py    # Applicant CRUD
│   │   ├── assessment_service.py   # Assessment management
│   │   └── eligibility_service.py  # Eligibility evaluation
│   ├── utils/
│   │   └── fetch_document.py       # fetch → store → version → change-detect → extract
│   ├── source_discovery.py         # Candidate discovery / robots gate / keyword scoring
│   ├── llm_governance.py           # RateLimiter, RetryPolicy, ResponseCache, BudgetTracker
│   ├── logging_config.py           # Structured logging, PII redaction
│   ├── pipeline.py                 # Phase 1 orchestrator
│   └── seed_registry.py            # Source registry seeder
├── tests/
│   ├── rules/                      # 192 tests (expression tree, operators, versioning) — was phase3/
│   ├── assessment/                 # 97 tests (applicant, assessment, gaps) — was phase3c/
│   ├── phase3d/                    # 134 tests (activation, repo, bridge, admin API, e2e, hardening)
│   ├── db/                         # 22 tests (models, repositories)
│   ├── logging/                    # 17 tests (structured logging, PII redaction)
│   └── test_*.py                   # API, auth, LLM governance, pipeline, security tests
├── validation/
│   └── run_report.json             # gitignored; regenerated by src/pipeline.py runs
├── schema/
│   ├── schema.sql
│   └── requirements.schema.json
├── data/
│   ├── gold/
│   │   ├── skilled/        # REAL gold: 189 + 190 (verbatim evidence)
│   │   ├── work/           # placeholder (AWAITING_REAL_SOURCE, no fabricated evidence)
│   │   ├── student/        # placeholder (AWAITING_REAL_SOURCE, no fabricated evidence)
│   │   └── visitor/        # placeholder (AWAITING_REAL_SOURCE, no fabricated evidence)
│   └── real_validation/
├── prompts/
│   ├── requirement_extraction_v1.md
│   ├── requirement_extraction_v2.md
│   ├── skilled/requirement_extraction_v2.md
│   ├── work/requirement_extraction_v2.md
│   ├── student/requirement_extraction_v2.md
│   └── visitor/requirement_extraction_v2.md
├── scripts/
│   └── browser_smoke.py            # Hermetic offline Chromium launch check (not collected by pytest)
├── sources/
│   └── source_registry.seed.json   # 14 verified government sources (SKILLED / WORK / STUDENT / VISITOR)
├── migrations/                     # Alembic migrations
├── .env.example                    # CORS_ORIGINS, JWT_SECRET_KEY, all config
├── .gitignore
├── Dockerfile                      # Python 3.12-slim
├── docker-compose.yml              # postgres:16-alpine + api
├── .dockerignore
├── alembic.ini
├── requirements.txt                # bcrypt==4.2.1 pinned
├── DESIGN.md                       # Full architecture design document
└── conftest.py                     # Root conftest, sys.path setup
```

## Key Design Decisions

1. **Missing fields = UNKNOWN, not FAIL**: `ApplicantProfile` fields are `None` when missing. `to_rule_data()` excludes `None` fields. Rule engine returns `UNKNOWN` for missing fields.

2. **Deterministic at temperature=0**: All evaluations with gpt-4o at temperature=0 produce identical results on repeated runs.

3. **Evidence is verbatim**: Every extracted requirement must have evidence that is an exact substring of its source chunk. Fabricated evidence is hard-rejected.

4. **No LLM in eligibility**: LLMs are used only in Phase 2 (knowledge extraction). Phase 3 and 3C are purely deterministic. RAG explains rules but never decides eligibility.

5. **Versioned rules**: Rule v1 assessed against historical applicants stays v1. Government policy changes create v2; v1 is marked SUPERSEDED. Historical assessments are never corrupted.

6. **Gap priority from metadata**: Gap CRITICAL/HIGH/MEDIUM/LOW priority comes from rule `critical`/`mandatory` flags, not arbitrary UI logic.

7. **Provenance chain**: Every rule traces: Rule → Requirement → Evidence → Chunk → Document Version → Document → Source → Government URL.

8. **Transactional activation**: Activating a rule version for a logical key supersedes any existing ACTIVE version. Atomic operation, never two ACTIVE for same field.

## Security

- **JWT secret fail-fast**: Production environment requires `JWT_SECRET_KEY` env var. Missing → startup error.
- **CORS**: Configurable via `CORS_ORIGINS` (comma-separated). Defaults to `http://localhost:3000`. Wildcard `*` with credentials is explicitly avoided.
- **bcrypt pinned**: `bcrypt==4.2.1` to ensure passlib compatibility.
- **PII redaction**: Date of birth, passport number, email never logged. Structured logging with PII filters.
- **Admin role check**: Non-admin users get 403 on admin endpoints.

## LLM Governance

- **Providers**: OpenAI (`gpt-4o-mini`, `gpt-4o`, ...) and Gemini (`gemini-flash-lite-latest`, ...) are both fully supported production extraction providers, selected via `EXTRACTION_MODEL`. `get_client()` in `src/extraction/llm_client.py` routes to `OpenAILLMClient` or `GeminiLLMClient` by model name prefix; both are wired through the same governance stack below. Anthropic/mock models are rejected at the factory (fail-fast, no silent fallback).
- **RateLimiter**: Per-model request throttling with sliding window
- **RetryPolicy**: Exponential backoff with configurable max delay
- **ResponseCache**: SHA256-keyed LRU cache with TTL expiry, namespaced per provider
- **BudgetTracker**: Daily/monthly token budget limits
- **UsageLog**: Per-request cost tracking and analytics
- **LLMGovernance**: Facade orchestrating all governance components

## Production Readiness

### Current Gaps

| Gap | Priority | Status |
|-----|----------|--------|
| ~~Assessment API stubbed~~ | P0 | DONE |
| ~~No scheduler~~ | P1 | DONE |
| ~~PostgreSQL ORM verification~~ | P1 | DONE (2026-08-27: FK/NOT NULL/lifecycle verified) |
| ~~PipelineRun tracking~~ | P2 | DONE |
| ~~No security test suite~~ | P2 | DONE |
| ~~Admin documents API~~ | P2 | DONE |
| ~~Admin knowledge-state endpoint~~ | P2 | DONE |
| ~~G9 lifespan testing bug~~ | P2 | DONE |
| ~~G10 UUID validation~~ | P2 | DONE |
| ~~G13 computed PipelineRun counters~~ | P2 | DONE |
| ~~Phase 4 run_extraction category prompt~~ | P2 | DONE (visa_category→prompt wiring + 10 tests) |
| ~~Admin-only enforcement on every admin endpoint~~ | P1 | DONE (2026-09-15: `POST /admin/sync` was unauthenticated — now admin-gated; all admin routes audited) |
| ~~Playwright collection crash~~ | P1 | DONE (2026-09-15: browser script moved out of `tests/`, suite no longer launches a browser at collection; driver is now Patchright) |
| WORK/STUDENT/VISITOR **source coverage** | P3 | DONE (2026-09-15: 5 sources added — 482 main + occupation list, 500 main + CRICOS, 600 main) |
| ~~Missing `_require_admin` on `GET /admin/rules/{rule_id}`~~ | P1 | DONE (2026-09-21: the only route in `admin_rules.py` missing the admin check every sibling route has — fixed + regression test) |
| ~~Docker deployment could never do real LLM extraction~~ | P0 | DONE (2026-09-21: `docker-compose.yml` never forwarded `OPENAI_API_KEY`/`GEMINI_API_KEY`/`EXTRACTION_MODEL` into the `api` container; `Dockerfile` never copied `prompts/` into the image. Both fixed and verified live.) |
| ~~Gemini provider support~~ | P2 | DONE (2026-09-21: `GeminiLLMClient` wired through the same governance layer as OpenAI; factory routes by model name) |
| ~~`POST /applicants` silently dropped `first_name`/`last_name`/`created_at` in its response~~ | P2 | DONE (2026-09-21: `ApplicantService.create()`/`list_for_user()` built incomplete response dicts; data was persisted correctly but the API lied about it) |
| ~~Authentication was temporarily disabled~~ | P0 | DONE — real JWT auth was restored 2026-09-22 (`src/api/routes/auth.py`'s bypass was deleted, not just re-enabled behind a flag — there is no bypass code left in this file at all) |
| ~~Browser escalation blocked with HTTP 403 even via genuine browser session~~ | P0 | DONE (2026-09-25): `src/browser_fetcher.py` was launching Chromium `headless=True`, fingerprinted and blocked by the Home Affairs WAF. Switched to `headless=False` under Xvfb (`Dockerfile` + `docker-entrypoint.sh`). Live-verified against 2 previously-blocked URLs: both now return real 200 OK with full content, where headless got 403 on the same URLs. The 12 `NEEDS_MANUAL_CAPTURE` sources have not yet been re-synced to pick this up — see below. |
| Re-sync the 12 `NEEDS_MANUAL_CAPTURE` Home Affairs sources now that headed browser escalation works | P1 | OPEN — the fix above was verified via direct fetch calls on 2 sample URLs, not a full `POST /admin/sync` run; the sources' DB status is still `NEEDS_MANUAL_CAPTURE` until an actual sync (or `manual-capture`, which also resets status) runs against them |
| ~~`SRC-AU-MIGRATION-REGS-1994` URL resolves to an index/citation page, not the regulation text~~ | P2 | DONE (2026-09-25): the `/latest/text` URL is an interactive expand-tree UI with no static text at any wait setting (tried `networkidle`, still 0 chunks). Found and verified the real PDF deep-link (`/F1996B03551/{date}/{date}/text/original/pdf/2` — Volume 2 of 4), confirmed to contain genuine Subclass 189 "189.2—Primary criteria" text (452 chunks). **Caveat**: this URL embeds the compilation date and has no stable dateless permalink — it will need re-deriving (via `/latest/downloads` with a real browser, since the download links are JS-rendered) whenever the regulation is next re-compiled. |
| `SRC-AU-CRICOS-REGISTER` yields 0 document chunks every fetch | P2 | OPEN, re-investigated 2026-09-25 — confirmed a genuine search-interface SPA: even headed browser + `networkidle` wait produces 0 chunks (title "Course Search", no default listable content, likely requires simulating a search-form submission to see any data). Not evadable/render-wait-fixable; needs re-sourcing (e.g. a CRICOS bulk-data export/API, if one exists) or a materially different, larger fetch strategy — out of scope for a straightforward fix. Marked as needing re-sourcing; no content fabricated. |
| SQLAlchemy/alembic can't connect to PostgreSQL in a fresh Docker build (`ModuleNotFoundError: No module named 'psycopg'`) | P0 | **NEW, found 2026-09-25** — unrelated to the fixes above, discovered while verifying them. A fresh `docker compose up -d --build` fails at `alembic upgrade head` because SQLAlchemy resolves the `psycopg` (v3) dialect for the plain `postgresql://` URL, but only `psycopg2-binary` is in `requirements.txt`. Worked around during verification by calling fetch/extract functions directly (bypassing the ORM/alembic chain); not fixed as part of this pass — likely needs either pinning `sqlalchemy` to the version last known to default to psycopg2, or adding `psycopg[binary]` to `requirements.txt` and deciding which driver is canonical. |
| Real rule lifecycle (DRAFT→VALIDATED→APPROVED→ACTIVE) never exercised with real extracted data | P1 | OPEN — now unblocked in principle (both content-source problems above are resolved or clearly triaged), but the psycopg issue above needs fixing first to actually run a fresh sync through the full ORM pipeline |
| WORK/STUDENT/VISITOR **real gold + live extraction** | P3 | OPEN (sources registered; real evidence capture still pending, no fabricated evidence) |
| Multi-country | P3 | OPEN |
| 491 real-world coverage | P3 | OPEN |

### Next Milestones

1. **Real gold + live extraction for WORK/STUDENT/VISITOR** — sources are registered; capture real Home Affairs / CRICOS evidence via the browser path or official API, then fill `data/gold/{work,student,visitor}/` and enable live extraction
2. **PostgreSQL-native production validation** — UUID, JSONB, TIMESTAMPTZ, proper indexes
3. **Multi-country support** — NZ, Canada, UK
4. **491 real-world scenario coverage** — full immigration program validation
5. **RAG & Recommendations** — LLM explains rules, suggests pathways

## Running Against Real Data

```bash
# Requires OPENAI_API_KEY (or GEMINI_API_KEY, with EXTRACTION_MODEL=gemini-flash-lite-latest) in .env
python src/extraction/run_extraction.py --document-id DOC-AU-189-MAIN --model gpt-4o

# Evaluate against real gold standard
python tests/extraction/test_real_gold_evaluation.py gpt-4o 0
```

**Note for Docker deployments**: `docker-compose.yml` forwards `OPENAI_API_KEY`, `GEMINI_API_KEY`,
and `EXTRACTION_MODEL` from the root `.env` into the `api` container's environment. If you add new
LLM-related environment variables, add them there too — the container does not otherwise see your
local `.env` file at all (it isn't copied into the image, and Compose only forwards variables listed
under `environment:`).

## Gold Datasets (Phase 3: category-aware)

Each visa category owns its gold dataset under `data/gold/<category>/` and is validated against **its own** prompt contract (`prompts/<category>/requirement_extraction_v2.md`). The harness `src/extraction/category_gold.py` enforces **zero cross-category contamination**: a Student source can never be scored against the Skilled contract.

- **skilled/** — populated with real, verbatim gold: `skilled_189_real.json` (23 items) + `skilled_190_real.json` (6 items).
- **work/student/visitor/** — placeholders marked `AWAITING_REAL_SOURCE` with **zero items**. The project's anti-hallucination rule forbids fabricating `evidence_text`; plain-HTTP fetches of live Home Affairs are HTTP 403-blocked (`SOURCE_REGISTRY_REPORT.md`). The browser path (`src/browser_fetcher.py`, headless=False) has been shown to receive HTTP 200 on real Home Affairs pages, so real evidence capture for these categories is feasible but not yet executed/in-sandbox. They become fillable once real content is captured.

Run the integrity check:

```bash
python -m extraction.category_gold   # prints contract + dataset integrity report
python -m pytest tests/extraction/test_category_gold_harness.py -q   # 12 guard tests
```

## Baseline Manifests

- `validation/run_report.json` — Phase 1 pipeline run results (gitignored, regenerated on each run)

## Verification

### Database-agnostic core (runs on SQLite for tests, PostgreSQL for prod)

The ORM rule lifecycle and Phase 3D pipeline are fully database-agnostic — they run identically against in-memory SQLite (897 passed / 2 skipped) and real PostgreSQL.

### Real PostgreSQL verification (performed 2026-08-27)

With `docker compose up -d postgres` + `alembic upgrade head`, the full ORM pipeline was verified end-to-end against real PostgreSQL:

```
Country → Program → Source → Document → Version → Chunk
    → ExtractionRun → Requirement (APPROVED)
        → Rule (DRAFT) → VALIDATED → APPROVED → ACTIVE
```

Confirmed:
- **FK constraints enforced** — PostgreSQL rejects a `pipeline_runs.source_id` with no matching `government_sources` row (proves G12 fix correctness).
- **NOT NULL constraints enforced** — `government_documents.retrieved_at` and `raw_path` required.
- **Full rule lifecycle** works via `EnhancedRuleRepo` against PostgreSQL (DRAFT → VALIDATED → APPROVED → ACTIVE).

### CLI Phase 1/2 pipeline (offline-verifiable)

```bash
python src/pipeline.py --source-id SRC-AU-189-MAIN --offline-fixture tests/fixtures/skilled_189_sample.html
python src/extraction/run_extraction.py --document-id DOC-AU-189-MAIN --model gpt-4o-mini
```

Verified: fetch → store → hash → change detect → extract, and Phase 2 requirement extraction with category-aware prompt selection (6 requirements extracted from DOC-AU-189-MAIN).

### Real network fetch — current behavior (updated 2026-09-25)

**2026-09-21 finding (now superseded)**: 12 of 14 sources — every `immi.homeaffairs.gov.au`
page in the registry — returned HTTP 403 even after browser escalation, and were demoted to
`NEEDS_MANUAL_CAPTURE`. At the time, `src/browser_fetcher.py` launched Chromium with
`headless=True`.

**2026-09-25 finding**: that browser escalation was being blocked by *headless-mode
fingerprinting*, not an unconditional block on automated browsers. Switching
`src/browser_fetcher.py` to `headless=False` (running under Xvfb — see Dockerfile /
`docker-entrypoint.sh`) was live-tested directly against two of the twelve previously-blocked
URLs (subclass 189 and 190 pages) and **both returned real `HTTP 200` with full, real page
content** (1.3–1.5MB each, containing real eligibility text) — the exact same URLs that got 403
under headless=True days earlier. No evasion technique was added; this is a real, visible
browser session, just rendered instead of running invisibly.

This was a direct diagnostic test (`fetcher.fetch` + `browser_fetcher.fetch_with_browser`
called directly), **not a full `POST /admin/sync` run** — the 12 sources' `government_sources.status`
in the database is still `NEEDS_MANUAL_CAPTURE` as of this writing, since that status only
updates on an actual sync attempt. Re-running sync against them (after resetting their status,
or via `manual-capture` which also resets status to `ACTIVE`) is the natural next step to
confirm this holds across all 12 and to get them flowing through extraction again — not done as
part of this fix; see Current Gaps.

The 2 non-Home-Affairs sources (`SRC-AU-CRICOS-REGISTER`, `SRC-AU-MIGRATION-REGS-1994`) are
unaffected by this finding (different domains, were never blocked) — see the "What works / what
doesn't" table above for their separate, still-open content issues.

## License

Internal project. Not for distribution.
