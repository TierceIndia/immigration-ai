# AU Immigration Knowledge Platform

End-to-end deterministic immigration eligibility system for Australian skilled migration visas.

**Phases completed**: 1 → 2 → 3 → 3C → 3D → Admin API → Admin Knowledge → DB Readiness + API Surface Hardening (all fully tested, 839 tests passing)

**Core principle**: Every eligibility decision is deterministic, auditable, and traceable to exact government evidence. No LLM decides eligibility.

**Production status**: NOT PRODUCTION-READY — see [Production Readiness](#production-readiness) for gaps.

## Quick Start

```bash
pip install -r requirements.txt
python -m pytest tests/ -q    # 839 passing
```

```bash
# Docker
docker-compose up -d
```

> **Filename guard:** the build context requires a file named exactly `Dockerfile`
> (capital D, no extension) at the project root. `docker-compose.yml` depends on it, and a
> case-only rename to `dockerfile` can silently reuse a stale image on case-insensitive
> filesystems — do not rename it.

The `api` container runs `alembic upgrade head` → `python src/seed_registry.py` (idempotent
seed of countries / programs / 9 verified government sources) → uvicorn. The Dockerfile
builds the Python 3.12-slim base plus Playwright's Chromium (for browser-based source
retrieval inside the `src/browser_fetcher.py` path).

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
python src/phase2/run_extraction.py --document-id DOC-AU-189-MAIN
python src/phase2/run_extraction.py --document-id DOC-AU-189-MAIN --model gpt-4o
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

### Admin Sync (`/api/v1/admin/sync`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Manually trigger government source sync |

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

```bash
python -m pytest tests/ -q              # Full suite: 731 tests
python -m pytest tests/phase3 -q        # Phase 3 core: 192 tests
python -m pytest tests/phase3c -q       # Phase 3C eligibility: 97 tests
python -m pytest tests/phase3d -q       # Phase 3D activation: 134 tests
python -m pytest tests/db -q            # Database models/repos: 22 tests
python -m pytest tests/phase2/test_category_prompts.py -q  # Category→prompt wiring: 10 tests
python -m pytest tests/test_api.py -q   # API routes: 35 tests
python -m pytest tests/test_auth.py -q  # Authentication: 11 tests
python -m pytest tests/test_security.py -q  # Security: 40 tests
python -m pytest tests/test_llm_governance.py -q  # LLM governance: 30 tests
python -m pytest tests/test_scheduler.py -q  # Scheduler: 7 tests
python -m pytest tests/test_pipeline_runs.py -q  # PipelineRun tracking: 42 tests
python -m pytest tests/test_e2e_scenarios.py -q  # End-to-end scenarios: 14 tests
```

| Layer | Tests | What it proves |
|-------|-------|----------------|
| Phase 2 | 51 + 10 + 12 | Schema validation, evidence checking, normalization, dedup + deterministic category→prompt wiring + category-aware gold harness |
| Phase 3 | 192 | Operators, expression tree, IF/THEN, exceptions, dependencies, versioning |
| Phase 3C | 97 | Applicant model, applicability, assessment, gap engine, questions |
| Phase 3D | 134 | Rule persistence, status machine, requirement→rule bridge, admin API, e2e, hardening |
| Database | 22 | ORM models, repositories, CRUD, in-memory SQLite |
| Logging | 17 | Structured logging, PII redaction, request context |
| API Routes | 35 | Request/response contracts, error handling, real assessment, G9 regression |
| Auth | 11 | JWT tokens, register/login, password hashing, duplicate email |
| LLM Governance | 30 | Rate limiting, caching, budget tracking, retry policy |
| Security | 40 | JWT secrets, PII redaction, CORS, authorization, password security |
| Scheduler | 7 | APScheduler start/stop, manual sync trigger |
| PipelineRun | 42 | Sync audit trail, repo CRUD, API endpoints, computed counters, orchestrator integration |
| E2E Scenarios | 14 | Multi-program, multi-version, gap engine, evaluation, orchestration |
| **Total** | **731** | **All passing, 0 failures** |

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
│   │   │   ├── auth.py             # Register/login/me
│   │   │   ├── applicants.py       # Applicant CRUD (JWT-protected)
│   │   │   ├── assessments.py      # Assessment create/list/evaluate
│   │   │   ├── admin_rules.py      # Admin review/approve/activate
│   │   │   ├── admin_requirements.py # Admin requirement review
│   │   │   ├── admin_sources.py    # Source admin API
│   │   │   ├── admin_documents.py  # Document admin API (G14)
│   │   │   ├── admin_knowledge.py  # Knowledge-state endpoint (G15)
│   │   │   ├── pipeline_runs.py    # Pipeline run API (G13 computed counters)
│   │   │   └── health.py           # Health check
│   │   └── schemas/
│   │       └── models.py           # Pydantic request/response models
│   ├── db/                         # SQLAlchemy ORM layer
│   │   ├── connection.py           # Engine, session factory, session_scope
│   │   ├── models.py               # 25+ ORM models
│   │   └── repositories.py         # 20+ repository classes
│   ├── phase1/                     # Government source registry
│   ├── phase2/                     # Knowledge extraction (LLM, validation, dedup)
│   ├── phase3/                     # Rule engine (operators, expressions, compiler)
│   │   ├── rule_activation.py      # Status machine (DRAFT→ACTIVE lifecycle)
│   │   ├── rule_repository.py      # EnhancedRuleRepo (CRUD, versioning, activation)
│   │   └── requirement_to_rule.py  # Approved requirement → rule compilation bridge
│   ├── phase3c/                    # Eligibility + Gap Engine
│   ├── orchestration/
│   │   └── government_sync.py      # Source sync orchestrator (on-demand)
│   ├── services/
│   │   ├── auth_service.py         # JWT, bcrypt, secret management
│   │   ├── applicant_service.py    # Applicant CRUD
│   │   ├── assessment_service.py   # Assessment management
│   │   └── eligibility_service.py  # Eligibility evaluation
│   ├── llm_governance.py           # RateLimiter, RetryPolicy, ResponseCache, BudgetTracker
│   ├── logging_config.py           # Structured logging, PII redaction
│   ├── pipeline.py                 # Phase 1 orchestrator
│   └── seed_registry.py            # Source registry seeder
├── tests/
│   ├── phase3/                     # 192 tests (expression tree, operators, versioning)
│   ├── phase3c/                    # 97 tests (applicant, assessment, gaps)
│   ├── phase3d/                    # 134 tests (activation, repo, bridge, admin API, e2e, hardening)
│   ├── db/                         # 22 tests (models, repositories)
│   ├── logging/                    # 17 tests (structured logging, PII redaction)
│   └── test_*.py                   # API, auth, LLM governance, pipeline, security tests
├── validation/
│   ├── phase3_baseline_manifest.json
│   └── run_report.json
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
├── sources/
│   └── source_registry.seed.json   # 9 verified government sources (SKILLED only)
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

- **RateLimiter**: Per-model request throttling with sliding window
- **RetryPolicy**: Exponential backoff with configurable max delay
- **ResponseCache**: SHA256-keyed LRU cache with TTL expiry
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
| WORK/STUDENT/VISITOR sources | P3 | OPEN |
| Multi-country | P3 | OPEN |
| 491 real-world coverage | P3 | OPEN |

### Next Milestones

1. **WORK/STUDENT/VISITOR source registry** — Add verified government sources for all visa categories
2. **PostgreSQL-native production validation** — UUID, JSONB, TIMESTAMPTZ, proper indexes
3. **Multi-country support** — NZ, Canada, UK
4. **491 real-world scenario coverage** — full immigration program validation
5. **RAG & Recommendations** — LLM explains rules, suggests pathways

## Running Against Real Data

```bash
# Requires OPENAI_API_KEY in .env
python src/phase2/run_extraction.py --document-id DOC-AU-189-MAIN --model gpt-4o

# Evaluate against real gold standard
python tests/phase2/test_real_gold_evaluation.py gpt-4o 0
```

## Gold Datasets (Phase 3: category-aware)

Each visa category owns its gold dataset under `data/gold/<category>/` and is validated against **its own** prompt contract (`prompts/<category>/requirement_extraction_v2.md`). The harness `src/phase2/category_gold.py` enforces **zero cross-category contamination**: a Student source can never be scored against the Skilled contract.

- **skilled/** — populated with real, verbatim gold: `skilled_189_real.json` (23 items) + `skilled_190_real.json` (6 items).
- **work/student/visitor/** — placeholders marked `AWAITING_REAL_SOURCE` with **zero items**. The project's anti-hallucination rule forbids fabricating `evidence_text`; live Home Affairs is HTTP 403-blocked to automated fetchers (`SOURCE_REGISTRY_REPORT.md`), so real evidence for these categories cannot be captured in-sandbox yet. They become fillable once real content is available.

Run the integrity check:

```bash
python -m phase2.category_gold   # prints contract + dataset integrity report
python -m pytest tests/phase2/test_category_gold_harness.py -q   # 12 guard tests
```

## Baseline Manifests

- `validation/phase3_baseline_manifest.json` — Phase 3 frozen checkpoint
- `validation/run_report.json` — Phase 1 pipeline run results

## Verification

### Database-agnostic core (runs on SQLite for tests, PostgreSQL for prod)

The ORM rule lifecycle and Phase 3D pipeline are fully database-agnostic — they run identically against in-memory SQLite (731 tests) and real PostgreSQL.

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
python src/phase2/run_extraction.py --document-id DOC-AU-189-MAIN --model mock-llm-v1
```

Verified: fetch → store → hash → change detect → extract, and Phase 2 requirement extraction with category-aware prompt selection (6 requirements extracted from DOC-AU-189-MAIN).

### Real network fetch — current behavior

`https://immi.homeaffairs.gov.au/...` returns **HTTP 403 ACCESS_BLOCKED** to automated fetchers. The fetcher correctly classifies the block, records it in `fetch_log`, and demotes the source status to `ACCESS_BLOCKED` — the designed failure-safe behavior. A real live end-to-end fetch therefore requires running the fetcher from an approved network context (or using the offline fixture / official API).

## License

Internal project. Not for distribution.
