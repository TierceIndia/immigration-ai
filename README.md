# AU Immigration Knowledge Platform

End-to-end deterministic immigration eligibility system for Australian skilled migration visas.

**Phases completed**: 1 → 2 → 3 → 3C → 3D → Admin API → Admin Knowledge → DB Readiness + API Surface Hardening → security hardening → browser-fetch escalation → Gemini provider support → Patchright migration → requirement_id/rule-uniqueness bug fixes → raw-snapshot persistence → folder rename (`extraction`/`rules`/`assessment`/`fetch`/`change_detection`) → minimal admin frontend → fetch politeness (per-host spacing + refusal cooldown + optional static proxy) (all fully tested, **1250 passing / 13 skipped / 3 pre-existing unrelated failures** — see [Test Suite](#test-suite)).

**Core principle**: Every eligibility decision is deterministic, auditable, and traceable to exact government evidence. No LLM decides eligibility.

**Production status**: NOT PRODUCTION-READY — see [Production Readiness](#production-readiness) for gaps.

**Proven for real, 2026-10-05**: `SKILLED_189` now has 3 real ACTIVE rules compiled from real,
LLM-extracted requirements with real evidence text, and a real applicant has been assessed against
them with a real `ruleset_hash`/`rule_version` and correct PASS/UNKNOWN/gap output — not test
fixtures. See `PLAN.md` §6 for the full Definition-of-Done checklist (5 of 6 boxes now checked).

## ✅ Authentication is real and enforced (restored 2026-09-22)

`get_current_user()` in [`src/api/routes/auth.py`](src/api/routes/auth.py) performs real JWT
validation — there was a temporary bypass for manual testing between 2026-09-21 and
2026-09-22, but it was fully deleted (not just toggled off) once that testing was done. Every
admin route enforces 401 (no/invalid token) → 403 (valid token, non-admin role) → 200 (admin) —
live-verified.

## What works / what doesn't (2026-10-05)

| Area | Status |
|------|--------|
| Deterministic rule engine + eligibility/gap engine | WORKING — **now proven with real (non-fixture) data**: `SKILLED_189`'s 3 real ACTIVE rules correctly assessed a real applicant (see banner above) |
| Admin API (rules, requirements, documents, sources, runs, knowledge-state, schema) | WORKING, real auth enforced (401/403/200 live-verified) |
| Minimal admin frontend (`frontend/`) | **NEW 2026-10-05** — plain React + Vite: login, sources dashboard, pipeline runs, "run sync now" button with background-job polling. Read-only, no new backend routes. Not the applicant-facing product UI (still not started — see `PLAN.md` Move 10). |
| `POST /api/v1/admin/sync` | WORKING — always re-fetches due sources, correctly demotes blocked sources, records real `PipelineRun` audit trail |
| Source registry | WORKING — 14 sources across SKILLED / WORK / STUDENT / VISITOR, all `ACTIVE` as of the 2026-10-05 full-registry sync |
| Source fetch → store → version → change-detect → downstream extraction | WORKING end-to-end against the real running stack, **and raw snapshots are now durably persisted** (see Current Gaps — this was broken until 2026-10-05) |
| Plain-HTTP fetch of `immi.homeaffairs.gov.au` | NOT WORKING — real HTTP 403 (WAF). Expected; this is what the browser-escalation path exists for. |
| Browser-escalation fetch of `immi.homeaffairs.gov.au` | **Migrated to Patchright, headless (2026-10-01).** ⚠️ **Not a reliable fix for WAF blocking** — live-verified 2026-10-05 that this WAF blocks *intermittently under per-URL request volume*, independent of headless/headed or Playwright/Patchright: a full 14-source sync got 0/14 failures, but a URL hit repeatedly afterward progressively degraded to a real 403. See Current Gaps. |
| Fetch politeness (per-host spacing + refusal cooldown) | **NEW 2026-10-09** — same-host requests are spaced by `FETCH_MIN_INTERVAL_SECONDS` (8) plus up to `FETCH_JITTER_SECONDS` (4) of random jitter; a host that answers 403/429 pauses **every** source on it for `FETCH_COOLDOWN_HOURS` (6). Changes *when* we ask, never how — no User-Agent or IP rotation was added. See "Fetch politeness guard". |
| LLM extraction — **OpenAI** | WORKING when the account has billing credits; fails safely and visibly (`FAILED` run, real error) when it doesn't |
| LLM extraction — **Gemini** | WORKING for most content — `EXTRACTION_MODEL=gemini-flash-lite-latest`, fully wired to the same governance layer as OpenAI. **New gap found 2026-10-05**: on `SRC-AU-600-MAIN`'s real content, all 16 candidate chunks came back with schema-violating JSON (wrong keys, invalid enums) — 0/16 succeeded, vs. 13/13 and 2/2 on other real sources. Not yet investigated. |
| Real requirement extraction end-to-end | **PROVEN WITH REAL CONTENT 2026-10-05** — `SRC-AU-MIGRATION-REGS-1994` (13 real requirements) and `SKILLED_189`'s other sources (2 real requirements) both extracted real, evidence-backed requirements via a real sync. `SRC-AU-CRICOS-REGISTER` still returns 0 (genuine JS-only SPA, confirmed unrecoverable via render-wait). `SRC-AU-600-MAIN`'s 0-chunk bug is fixed (see Current Gaps) but hit the new Gemini schema gap above instead. |
| Rule compilation → review → activation → assessment | **PROVEN WITH REAL DATA 2026-10-05** — `SKILLED_189`'s 2 real requirements reviewed, approved, compiled into 3 real rules, validated/approved/activated, and correctly assessed against a real applicant (real `ruleset_hash`/`rule_version`, correct PASS/UNKNOWN/gaps) |
| Applicant CRUD | WORKING |
| Docker deployment | WORKING — LLM key forwarding, `prompts/` copying, the psycopg v2/v3 dialect drift, and the Patchright/Xvfb migration are all fixed; raw snapshots now persist across container recreation (volume added 2026-10-05) |
| WORK / STUDENT / VISITOR **live LLM extraction + gold data** | NOT WORKING YET — sources registered, but real evidence capture still pending (`AWAITING_REAL_SOURCE`); no fabricated evidence |
| Multi-country (NZ/CA/UK) | NOT STARTED |

## Quick Start

```bash
pip install -r requirements.txt
python -m pytest tests/ -q    # 1250 passed / 13 skipped / 3 failed (pre-existing, environment-only — see Test Suite)
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
deliberately not installed (see `src/fetch/browser_fetcher.py` for the rationale).

Diagnostics: `GET /health` is a pure liveness probe (always 200). `GET /ready` reports
DB connectivity — 200 when connected, else 503 with safe, credential-scrubbed context
(`configured_backend` / `configured_host` / `configured_database`, plus a scrubbed
`db_error_detail` outside TESTING). Credentials are scrubbed before they can appear in the
response body.

Scheduler: government source sync runs **once daily at a fixed clock time** (UTC) by default,
not on a random/interval offset. The env vars (`GOVERNMENT_SYNC_HOUR_UTC` 0-23 default `3`,
optional `GOVERNMENT_SYNC_MINUTE_UTC` 0-59 default `0`, legacy `GOVERNMENT_SYNC_INTERVAL_MINUTES`)
are only the startup default — a schedule set via `PUT /api/v1/admin/scheduler` is persisted
(`scheduler_settings`) and overrides them on every subsequent restart. Manual sync is always
available via `POST /api/v1/admin/sync` (all sources) or `POST /api/v1/admin/sources/{id}/sync`
(one source) — both share the same lock, so only one sync ever runs at a time.

A static DB structure snapshot is maintained in [`DBSTRUCTURE.md`](DBSTRUCTURE.md) (the
dev-only `GET /api/v1/admin/schema-report` introspection route was removed — unused outside
its own tests, see DESIGN.md's route-removal note).

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
| `POST` | `/register` | Register new user (409 on duplicate email). Gated behind `ALLOW_PUBLIC_REGISTRATION` (default `false` — 403 when disabled); admin accounts are bootstrapped separately via `ADMIN_EMAIL`/`ADMIN_PASSWORD`, never through this route |
| `POST` | `/login` | Login → JWT token |
| `GET` | `/me` | Current user info (includes live DB role, not the JWT claim) |

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

All mutations are admin-only (`Depends(require_admin)`) and record the **authenticated**
admin as the actor in `rule_versions.created_by` — there is no client-supplied `actor` field
at all; a request body cannot name a different actor even accidentally.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/queue` | Review queue grouped by status (drafts/validated/approved_pending/active + counts) |
| `GET` | `/` | List all rules, optional `?status=`/`?program_id=` filters |
| `GET` | `/{id}` | Get rule |
| `POST` | `/{id}/validate` | DRAFT → VALIDATED |
| `POST` | `/{id}/approve` | VALIDATED → APPROVED |
| `POST` | `/{id}/activate` | APPROVED → ACTIVE (transactional supersession) |
| `POST` | `/{id}/reject` | Pre-active → REJECTED |
| `POST` | `/{id}/retire` | SUPERSEDED/ACTIVE → RETIRED |
| `POST` | `/batch/validate`, `/batch/approve`, `/batch/activate` | Same transitions, by a list of rule_ids |

### Admin Requirements (`/api/v1/admin/requirements`)

Review actions are admin-only and record the **authenticated** admin in
`requirement_review.reviewer` — same as rules' `actor` above, there is no client-supplied
`reviewer` field.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/review-queue` | Requirements awaiting review (`?status=`, default `NEEDS_REVIEW`; `?program_id=`). Each item includes description, confidence, evidence text, source document/version, and — for a CONFLICT — the conflicting `previous_value` |
| `GET` | `/{id}` | Get requirement detail |
| `POST` | `/review/{id}/approve` | Mark requirement APPROVED |
| `POST` | `/review/{id}/reject` | Mark requirement REJECTED |
| `POST` | `/review/{id}/mark-needs-review` | Mark requirement NEEDS_REVIEW |

### Admin Documents (`/api/v1/admin/documents`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List documents (filter by source, program, status) |
| `GET` | `/{document_id}` | Get document metadata |
| `GET` | `/{document_id}/versions` | List version history (newest first) |
| `GET` | `/{document_id}/chunks` | List text chunks (filter by version) |
| `GET` | `/{document_id}/diff` | Diff between two document versions |

### Pipeline Runs (`/api/v1/admin/runs`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{id}` | Get a single pipeline run or background sync job's status (used to poll a Run now/Run all sync to completion) |

The list/summary/per-source history routes that used to live here were removed — the admin
frontend has no pipeline-runs page (see DESIGN.md's route-removal note).

### Admin Sources (`/api/v1/admin/sources`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List all government sources (admin only) |
| `POST` | `/` | Create a source (admin only) |
| `GET` | `/{source_id}` | Get a single source (admin only) |
| `PATCH` | `/{source_id}` | Update source — `status` limited to `ACTIVE`/`DISABLED`/`NEEDS_MANUAL_CAPTURE`, 422 otherwise (admin only) |
| `POST` | `/{source_id}/sync` | Sync just this one source in the background — shares the same lock as `/admin/sync`, 409 if any sync is running. Poll via `GET /admin/runs/{run_id}` |
| `POST` | `/discover` | Run source discovery (admin only, API-only — no frontend page yet) |
| `GET` | `/discovered` | List discovered source candidates (admin only, API-only) |
| `POST` | `/discovered/{id}/approve` | Approve a candidate into a real source (admin only, API-only) |
| `POST` | `/discovered/{id}/reject` | Reject a candidate (admin only, API-only) |

### Admin Scheduler (`/api/v1/admin/scheduler`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Current global sync schedule (enabled, mode, hour/minute or interval) + computed `next_run_at` |
| `PUT` | `/` | Change the schedule — validates hour 0-23/minute 0-59/interval ≥15, persists it (`scheduler_settings`, survives a restart), and applies it to the live scheduler job immediately |

### Admin Applicants (`/api/v1/admin/applicants`)

Read-only, admin-only, cross-user view — separate from the owner-scoped `/applicants`/
`/assessments` routes above, which are unchanged.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | All applicants, paginated, each with its owner's email |
| `GET` | `/{id}` | One applicant's profile plus their full assessment history |

### Admin Sync (`/api/v1/admin/sync`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Manually trigger a sync of **all** active sources — **admin only** (401 without token, 403 for non-admins, 409 if a sync is already running) |

#### Does `/admin/sync` touch all 14 sources? (updated 2026-10-05)

`sync_all_sources()` in `src/orchestration/government_sync.py` only ever queries sources
where `status == "ACTIVE"`:

```python
sources = session.query(GovernmentSource).filter(GovernmentSource.status == "ACTIVE").all()
```

As of 2026-10-05, **all 14 seeded sources are `ACTIVE`** — a full-registry sync that day got
0/14 fetch failures. This is a change from earlier operation, where most
`immi.homeaffairs.gov.au` sources sat demoted in `NEEDS_MANUAL_CAPTURE` after `HTTP 403` even
through browser escalation. `_record_fetch_failure()` in `src/utils/fetch_document.py` still sets
`source.status = "NEEDS_MANUAL_CAPTURE"` whenever a block survives browser escalation — that logic
is unchanged and still deliberate (the project's design explicitly forbids trying harder to get
past a real block: no stealth plugins, no fingerprint evasion, no CAPTCHA solving) — it just isn't
firing right now because the WAF isn't categorically blocking these sources anymore.

**What's actually going on with the WAF now (important nuance)**: it blocks **intermittently,
apparently under per-URL request volume**, not categorically by domain or by headless/headed
browser signature. Live-verified 2026-10-05: hitting the same `SRC-AU-600-MAIN` URL repeatedly
during debugging made it progressively degrade (200 OK → 0 chunks → outright 403), while every
other URL touched only once in the same session stayed healthy. So a source going to
`NEEDS_MANUAL_CAPTURE` today is more likely a transient, request-volume-sensitive block than a
permanent one — a plain re-sync after a cooldown is the right first response, not manual capture.

**Note**: the `POST /admin/sources/{source_id}/manual-capture` endpoint described in earlier
versions of this doc has since been **removed** from the API (2026-10-01) along with its
`ManualCaptureRequest` model. If a source is demoted today, the recovery path is: wait for the WAF
to cool down, then re-trigger `POST /admin/sync` (or `PATCH` the source's `status` back to
`ACTIVE` directly if you're confident the block has lifted).

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

> **1250 passed / 13 skipped / 3 failed** (2026-10-09), real auth enforced throughout. The 3
> failures are pre-existing and environment-only, not caused by any work in this round: 2 are a
> **host-venv-only** `psycopg` v3 issue (importable but its native `pq` wrapper isn't — Docker is
> unaffected, see Current Gaps), 1 is `test_patchright_missing_returns_launch_failure` hitting a
> different failure path locally because Patchright's Chromium binary isn't downloaded on this host.

```bash
python -m pytest tests/ -q              # Full suite (counts above)
python -m pytest tests/rules -q         # Rule engine core: 208 tests
python -m pytest tests/assessment -q    # Eligibility + gap engine: 97 tests
python -m pytest tests/phase3d -q       # Rule activation/lifecycle: 121 tests
python -m pytest tests/db -q            # Database models/repos: 22 tests
python -m pytest tests/extraction -q    # Extraction (schema, evidence, dedup, gold, category prompts): 77 tests
python -m pytest tests/test_api.py -q   # API routes: 41 tests
python -m pytest tests/test_auth.py -q  # Authentication: 13 tests
python -m pytest tests/test_security.py -q  # Security: 44 tests
python -m pytest tests/test_llm_governance.py -q  # LLM governance: 32 tests
python -m pytest tests/test_scheduler.py -q  # Scheduler + background sync jobs: 38 tests
python -m pytest tests/test_e2e_scenarios.py -q  # End-to-end scenarios: 14 tests
```

| Layer | Tests | What it proves |
|-------|-------|----------------|
| Rule engine (`tests/rules/`) | 208 | Operators, expression tree, IF/THEN, exceptions, dependencies, versioning |
| Assessment/gap engine (`tests/assessment/`) | 97 | Applicant model, applicability, assessment, gap engine, questions |
| Rule activation/lifecycle (`tests/phase3d/`) | 121 | Rule persistence, status machine, requirement→rule bridge (incl. the category-uniqueness fix), admin API, e2e, hardening |
| Extraction (`tests/extraction/`) | 77 | Schema validation, evidence checking, normalization, dedup, extraction, category→prompt wiring, gold harness, requirement_id length guard |
| Database | 22 | ORM models, repositories, CRUD, in-memory SQLite |
| Logging | 17 | Structured logging, PII redaction, request context |
| API Routes | 41 | Request/response contracts, error handling, real assessment, admin-sync 202/409 background-job pattern, source PATCH validation |
| Auth | 13 | JWT tokens, register/login, password hashing, duplicate email, role in token |
| LLM Governance | 32 | Rate limiting, caching, budget tracking, retry policy |
| Security | 44 | JWT secrets, PII redaction, CORS, authorization, password security |
| Scheduler | 38 | APScheduler start/stop, manual sync trigger, concurrent-sync lock, background sync job + lock-release ordering |
| E2E Scenarios | 14 | Multi-program, multi-version, gap engine, evaluation, orchestration |
| Fetch / sync / admin / integration (everything else) | ~334 | fetch→store→version, change detection, raw-snapshot persistence + durability, refetch lifecycle, crawl-frequency gating, Patchright browser escalation, source PATCH/no-manual-capture, driver pinning, form-wrapped-content extraction, lineage integrity |
| Fetch politeness (`tests/test_politeness.py`) | 46 | Per-host spacing, jitter window, crawl-delay, Retry-After, virtual-clock + thread safety, shared by the plain and browser paths |
| Host cooldowns (`tests/test_host_cooldown.py`) | 53 | Refusal classification (403/429 vs faults), pause window/expiry/hits, sync skip, API `409 host_cooldown` + `blocked_until`, no-override, refusal never changes how we fetch |
| Static proxy (`tests/test_proxy.py`) | 32 | Default off, fail-closed on malformed config, no credential leak, wiring into both paths, static-never-rotated |
| **Total** | **1266 collected** | **1250 passed / 13 skipped / 3 failed (2026-10-09), real auth enforced throughout.** |

(The per-layer counts above are the representative 2026-09-15 snapshot and do not individually
reconcile with the current total — several layers have grown since; the new politeness / cooldown /
proxy rows are exact.)

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
│   │   │   ├── admin_sources.py    # Source admin API + discovery
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
├── frontend/                        # Minimal read-only admin console (plain React + Vite)
│   ├── src/
│   │   ├── pages/                  # LoginPage, SourcesDashboard, PipelineRunsPage
│   │   ├── components/             # StatusBadge, SyncButton (202 + poll pattern)
│   │   └── api.js                  # fetch wrapper + JWT storage, no new backend routes
│   └── README.md                   # `npm install && npm run dev`, talks to :8000 by default
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

9. **Polite, non-evasive retrieval**: We change *when* we ask (per-host spacing + jitter, respect `Crawl-delay`/`Retry-After`, stop hitting a host that refused us) and *not how we identify ourselves*. There is no User-Agent rotation, no IP/proxy rotation, no CAPTCHA bypass and no stealth headers; the optional `FETCH_PROXY_URL` is a single static egress, not a rotation.

## Security

- **JWT secret fail-fast**: Production environment requires `JWT_SECRET_KEY` env var. Missing → startup error.
- **CORS**: Configurable via `CORS_ORIGINS` (comma-separated). Defaults to `http://localhost:3000`. Wildcard `*` with credentials is explicitly avoided.
- **bcrypt pinned**: `bcrypt==4.2.1` to ensure passlib compatibility.
- **PII redaction**: Date of birth, passport number, email never logged. Structured logging with PII filters.
- **Egress proxy fail-closed**: `FETCH_PROXY_URL` credentials are redacted from logs and error strings, and a malformed proxy URL causes affected fetches to be refused (reported as `proxy_config_error`) rather than silently sent direct.
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
| ~~Browser escalation blocked with HTTP 403 even via genuine browser session~~ | P0 | **Superseded understanding (2026-10-05)** — the 2026-09-25 "headed=fix" claim did not hold up (`DESIGN.md` §16 correction, 2026-10-01). Current finding: the WAF blocks intermittently under per-URL request volume, independent of headless/headed or Playwright/Patchright. See the dedicated section below and `PLAN.md` Known Blocker #7. |
| ~~`SRC-AU-MIGRATION-REGS-1994` URL resolves to an index/citation page~~ | P2 | DONE (2026-09-25), **re-confirmed live 2026-10-05**: real sync extracted 13 real requirements from the fixed PDF deep-link, with intact (non-truncated) IDs. Caveat unchanged — URL has no stable dateless permalink. |
| `SRC-AU-CRICOS-REGISTER` yields 0 document chunks every fetch | P2 | OPEN — confirmed a genuine search-interface SPA, not render-wait-fixable; needs re-sourcing (e.g. a CRICOS bulk-data export/API) or a materially different fetch strategy. |
| ~~SQLAlchemy/alembic can't connect to PostgreSQL in a fresh Docker build~~ | P0 | DONE (2026-10-01): `DATABASE_URL` now names `postgresql+psycopg2://` explicitly. ⚠️ Residual: 2 tests fail in the **host venv only** (`psycopg` v3 importable, native `pq` wrapper isn't) — Docker is unaffected, not yet cleaned up. |
| ~~Real rule lifecycle never exercised with real extracted data~~ | P0 | **DONE 2026-10-05** — `SKILLED_189` has 3 real ACTIVE rules from real extracted requirements, correctly assessed against a real applicant. See the banner at the top of this file and `PLAN.md` §6. |
| ~~Raw immutable snapshots not persisted in the production path~~ | P1 | **DONE 2026-10-05** — `docker-compose.yml` had no volume for `/app/raw`; every container recreation silently lost previously-fetched files. Added a `raw_snapshots` named volume; verified a fetch's file survives a recreate with its hash matching the DB. |
| ~~`uq_rules_active_per_field` only covers (program_id, field)~~ | P1 | **DONE 2026-10-05** — narrower than `activate_rule`'s own `(program_id, category, field)` logical key; a legitimate same-field-different-category ACTIVE rule was incorrectly rejected. Widened via migration `7c9e2a4f1b6d`, regression test added. |
| ~~`requirement_id`/`rule_id` truncation on PostgreSQL~~ | P0 | **DONE 2026-10-05** — `String(48)` silently dropped rows for long `program_id`s on Postgres (SQLite never caught it). Widened to `String(96)`, added a fail-fast length guard, migration `a3c5e7f9b1d3`. |
| `SRC-AU-600-MAIN` (and 6 other `immi.homeaffairs.gov.au` pages) yield 0 chunks | P1 | **Two separate bugs, not one.** (1) DONE 2026-10-05: the page's content sits inside a legacy ASP.NET `<form>` wrapper that `extractor_html.py` was deleting wholesale as noise — fixed by unwrapping, not decomposing, `<form>` tags. (2) **Bigger, found the same day**: that fix alone wasn't enough — a full-registry sync afterward still showed 7/14 sources at 0 chunks on a genuinely successful fetch. Root cause: these Angular pages serve real content as an escaped JSON blob in a hidden `<input>`, rendered into real DOM only *after* `domcontentloaded` — `browser_fetcher.py` was capturing too early. Fixed by waiting for a real structural selector before capturing (bounded 8s, falls through honestly for genuinely contentless pages). Unit-tested against the real captured shape; **live reconfirmation blocked right now** by the same WAF volume-sensitivity described below. |
| **NEW (2026-10-05)**: Gemini returns schema-violating JSON for `SRC-AU-600-MAIN`'s real content | P2 | OPEN — found only because the 0-chunk bug above is fixed; all 16 of this page's candidate chunks fail schema validation (wrong keys, invalid enums), vs. 13/13 and 2/2 success on other real sources. Not yet investigated. |
| **NEW (2026-10-05)**: `test_patchright_missing_returns_launch_failure` fails locally | P3 | OPEN — test/environment mismatch: Patchright is installed on this host but its Chromium binary isn't downloaded, so the test hits a different failure path than the one it asserts. |
| WORK/STUDENT/VISITOR **real gold + live extraction** | P3 | OPEN (sources registered; real evidence capture still pending, no fabricated evidence) |
| Multi-country | P3 | OPEN |
| 491 real-world coverage | P3 | OPEN |
| Applicant-facing frontend | P2 | OPEN — a separate **minimal admin frontend** now exists (`frontend/`, 2026-10-05: login, sources, pipeline runs), but the applicant-facing product UI is still not started |

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
- **work/student/visitor/** — placeholders marked `AWAITING_REAL_SOURCE` with **zero items**. The project's anti-hallucination rule forbids fabricating `evidence_text`; plain-HTTP fetches of live Home Affairs are HTTP 403-blocked (`SOURCE_REGISTRY_REPORT.md`). The browser path (`src/fetch/browser_fetcher.py`, Patchright headless) does reach real Home Affairs pages some of the time (see [Real network fetch — current behavior](#real-network-fetch--current-behavior-updated-2026-10-05) — it's intermittent, not guaranteed), so real evidence capture for these categories is feasible but not yet executed. They become fillable once real content is captured.

Run the integrity check:

```bash
python -m extraction.category_gold   # prints contract + dataset integrity report
python -m pytest tests/extraction/test_category_gold_harness.py -q   # 12 guard tests
```

## Baseline Manifests

- `validation/run_report.json` — Phase 1 pipeline run results (gitignored, regenerated on each run)

## Verification

### Database-agnostic core (runs on SQLite for tests, PostgreSQL for prod)

The ORM rule lifecycle and rule-activation pipeline are fully database-agnostic — they run identically against in-memory SQLite (1250 passed / 13 skipped, see [Test Suite](#test-suite)) and real PostgreSQL (re-verified live 2026-10-05, including the two new migrations for the requirement_id/rule_id widening and the ACTIVE-rule category fix).

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

### Real network fetch — current behavior (updated 2026-10-05)

**2026-09-21 finding**: 12 of 14 sources — every `immi.homeaffairs.gov.au` page in the registry —
returned HTTP 403 even after browser escalation, and were demoted to `NEEDS_MANUAL_CAPTURE`. At
the time, `src/browser_fetcher.py` launched Chromium (Playwright) with `headless=True`.

**2026-09-25 finding (since corrected — see below)**: switching to `headless=False` under Xvfb
appeared to fix it in a 2-URL direct test. **This did not hold up.** `DESIGN.md` §16 already
carries a 2026-10-01 correction: a genuine headless Patchright session was re-tested live against
the same URLs and still got 403 — headed mode never reliably defeated the WAF.

**2026-10-01**: the fetch layer was migrated off Playwright entirely onto **Patchright, running
headless** (no more Xvfb/`docker-entrypoint.sh` — see `Dockerfile`/`docker-compose.yml`). This was
a maintainability and correctness move (drops a redundant X-server dependency the headed-mode
theory never actually needed), not a WAF fix.

**2026-10-05 finding (current understanding)**: the WAF blocks **intermittently, correlated with
per-URL request volume**, independent of headless/headed or Playwright/Patchright. Evidence:
a full 14-source sync that day got **0/14 fetch failures** — every source, including every
`immi.homeaffairs.gov.au` page, came back `ACTIVE`/`OK`. Minutes later, directly re-hitting one of
those same URLs (`SRC-AU-600-MAIN`) repeatedly for debugging made it progressively degrade: a
normal 200, then a 200 with corrupted/empty content, then an outright 403 "even via a genuine
browser session" — while every other URL in the registry, touched only once that session, stayed
healthy. This points at a per-URL rate limit rather than a categorical IP or fingerprint block.
**No evasion technique has been or will be added in response** — the correct move is understanding
the real trigger (see the IP-vs-fingerprint diagnostic task in `PLAN.md` Known Blocker #7), not
adding retries or stealth.

The 2 non-Home-Affairs sources (`SRC-AU-CRICOS-REGISTER`, `SRC-AU-MIGRATION-REGS-1994`) are on
different domains and have never been subject to this WAF at all — see the "What works / what
doesn't" table above for their separate, content-level issues.

### Fetch politeness guard (added 2026-10-09)

The 2026-10-05 finding above was that the WAF degrades under **per-URL request volume** — not a
fingerprint block. The response is therefore to change *when* we ask, not *how*: two rules, plus
one opt-in egress setting, all owned by `src/fetch/politeness.py`, `src/orchestration/host_cooldown.py`
and `src/fetch/proxy.py`. **No evasion was added** — `fetcher.USER_AGENT` is still the single fixed
string `AU-Immigration-Source-Monitor/0.1`, and there is still no rotation of any kind.

- **Per-host spacing with jitter.** Two requests to the **same host** are separated by at least
  `FETCH_MIN_INTERVAL_SECONDS` (default `8`) plus a random `uniform(0, FETCH_JITTER_SECONDS)`
  (default up to `4`) — so the pattern is not metronomic. Different hosts never delay each other,
  and the plain-HTTP and browser paths share the one limiter. A `robots.txt` `Crawl-delay` and a
  `Retry-After` header are honoured when present.
- **Refusal cooldown.** When a host answers **403 or 429** — the two statuses that mean *refused*,
  as opposed to a timeout/DNS/5xx *fault* — every source on that host is paused for
  `FETCH_COOLDOWN_HOURS` (default `6`). The sync layer skips paused hosts (both a full sync and a
  single-source "Run now"); the API returns `409` with a `host_cooldown` detail, and the admin
  Sources table shows a "Paused until" column. There is deliberately **no force/override flag** —
  a forced request is still a request the host asked us not to make. A successful fetch clears the
  pause.
- **Optional static egress proxy.** `FETCH_PROXY_URL` (default unset = direct) points every
  outbound fetch at **one fixed** proxy for reachability/egress control — a host that only allows a
  known exit IP, a corporate gateway, and so on. It is never rotated and never changes in response
  to a refusal. A malformed value **fails closed**: affected fetches are refused with an explicit
  `proxy_config_error` (never silently sent direct). Any `user:password@` credentials are redacted
  from logs and error strings.

**Expected sync duration.** With the defaults above, requests to one host are ~`8 + uniform(0,4)`s
apart (average ≈ `10`s); the *first* request to a host is immediate. So a run over the 10
`immi.homeaffairs.gov.au` sources takes roughly:

| Same-host requests | Minimum (jitter = 0) | Average (jitter ≈ 2s) | Maximum (jitter = 4s) |
|--------------------|----------------------|-----------------------|-----------------------|
| 10 (9 gaps)        | `9 × 8 = 72s`        | `9 × 10 = 90s`        | `9 × 12 = 108s`       |

That is the intended trade — **~90s on average instead of the ~3.4s / ~3 req/s the registry-wide
sync measured before this change** — and it is a *per host* figure: the 2 other-domain sources run
concurrently on their own host clock. A refusal makes the run **shorter**, not longer: after the
first 403/429 the host is skipped for the rest of the run (and for the next `FETCH_COOLDOWN_HOURS`),
so no further requests are sent at all.

## License

Internal project. Not for distribution.
