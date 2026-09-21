# Immigration Knowledge Platform — Full Architecture Design

## 0. Scope

End-to-end deterministic immigration eligibility system for Australian skilled migration visas.

- **Country**: Australia
- **Visas**: 189 (Skilled Independent), 190 (Skilled Nominated), 491 (Skilled Work Regional)
- **Authorities**: Department of Home Affairs, Federal Register of Legislation
- **Core principle**: Every eligibility decision is deterministic, auditable, traceable to government evidence

### Phases

| Phase | Purpose | Status |
|-------|---------|--------|
| 1 | Data Ingestion (source registry, fetch, extract, hash, change detect) | COMPLETE |
| 2 | Knowledge Extraction (LLM requirement extraction, evidence validation, human review) | COMPLETE |
| 3 | Deterministic Rule Engine (operators, expressions, IF/THEN, exceptions, dependencies, versioning) | COMPLETE |
| 3C | Eligibility + Gap Engine (applicant model, applicability, assessment, gap detection, questions) | COMPLETE |
| 3D | Government Knowledge Activation (req→rule bridge, status machine, admin approval, e2e) | COMPLETE |
| API | FastAPI service layer (auth, CRUD, admin review, assessments, documents, knowledge-state) | COMPLETE |
| Infra | DB abstraction, Docker, LLM governance, structured logging | COMPLETE |
| 4+ | RAG, Recommendations, User Product, Multi-country | NOT STARTED |

---

## 1. Phase 1 — Data Ingestion

### Pipeline

```
SOURCE REGISTRY (seed JSON)
        │
        ▼
     FETCHER  ── HTTP GET, records status/headers/timing
        │
        ▼
  RAW STORAGE  ── immutable HTML/PDF bytes on disk, one snapshot per fetch date
        │
        ▼
  EXTRACTION   ── HTML → structured text (headings/paragraphs/lists/tables)
        │
        ▼
  CONTENT HASH ── sha256 of raw bytes AND of extracted clean text
        │
        ▼
CHANGE DETECTION ── hash comparison → NEW / UNCHANGED / CHANGED
        │
        ▼
   SQLITE METADATA STORE  ── sources, documents, versions, chunks, fetch_log
```

No LLM. No rules. Pure data engineering.

### Source Registry

14 verified government sources across all four visa categories — SKILLED (189/190/491 + Migration Regulations), WORK (482 main + Skill Occupation List), STUDENT (500 main + CRICOS register), VISITOR (600 main) — covering 7 programs in total.

### Change Detection

- `NEW`: No prior hash → create document + version 1
- `CHANGED`: Hash differs → bump version, re-extract
- `UNCHANGED`: Hash matches → update `last_checked_at`, skip re-extraction

---

## 2. Phase 2 — Knowledge Extraction

### Pipeline

```
document_chunks (Phase 1)
        │
        ▼
chunk_selector.py       — recall-oriented candidate detection
        │
        ▼
llm_client.py           — OpenAI only (production); MockLLMClient (unit tests only)
        │
        ▼
requirement_validator.py — JSON Schema + logic consistency
        │
        ▼
evidence_validator.py    — exact substring verification against source chunks
        │
        ▼
requirement_normalizer.py — controlled vocabulary for operator/unit/category
        │
        ▼
requirement_deduplicator.py — semantic dedup (EXACT_DUPLICATE / COMPLEMENTARY / CONFLICT)
        │
        ▼
requirement_repository.py  — persistence with version tracking
        │
        ▼
review.py                  — human approve/reject with audit trail
```

### Key Models

```
Government Source → Document → Document Version → Document Chunk → Requirement → Rule
```

### Evidence Anti-Hallucination

Every extracted requirement must have `evidence_text` that is an exact substring of its cited `source_chunk`. Fabricated/paraphrased evidence is hard-rejected.

### Gold Standard

Real gold datasets are organized **per visa category** under `data/gold/<category>/` and each is validated against its own prompt contract — enforcing **zero cross-category contamination** (a Student source is never scored with the Skilled contract).

- **skilled/** — populated with real, verbatim gold: `skilled_189_real.json` (23 items) + `skilled_190_real.json` (6 items). The 189 dataset's 17 evidence strings are verified verbatim substrings; it also carries annotations for the 190 cross-source set.
- **work/student/visitor/** — placeholders marked `AWAITING_REAL_SOURCE` with **zero items** (anti-hallucination: no fabricated `evidence_text`; plain-HTTP fetches of live Home Affairs are HTTP 403-blocked to automated fetchers, see `SOURCE_REGISTRY_REPORT.md`; the browser path `src/browser_fetcher.py` receives HTTP 200 with headless=False, so real capture is feasible but not yet executed).

The integrity harness `src/phase2/category_gold.py` fingerprints each category's prompt contract and every gold dataset, and `tests/phase2/test_category_gold_harness.py` (12 tests) asserts the contamination-free + no-fabricated-evidence invariants hold.

### Production Model

gpt-4o at temperature=0: F1=0.882, Evidence Accuracy=1.0, Critical Recall=1.0 (8/8).

---

## 3. Phase 3 — Deterministic Rule Engine

### Data Model

```
Rule
├── rule_id: str
├── requirement_id: str (traceability to Phase 2)
├── program_id: str (SKILLED_189 etc.)
├── category: str
├── field: str (field name in applicant data)
├── operator: str (EQ, LT, GTE, IN, etc.)
├── value: Any (comparison value)
├── unit: str | None
├── mandatory: bool
├── critical: bool
├── message: str
├── provenance: Provenance
│   ├── source_id → document_id → chunk_id → requirement_id
│   └── evidence_text (verbatim from source)
├── status: DRAFT | ACTIVE | SUPERSEDED | RETIRED
├── version: int
└── created_at: str (ISO 8601)
```

### Four-State Semantics

| Status | Meaning |
|--------|---------|
| PASS | Rule applies, condition passed |
| FAIL | Rule applies, condition failed |
| UNKNOWN | Rule applies, required information missing |
| NOT_APPLICABLE | Rule does not apply to this applicant/pathway |

### Operators (12)

`EQ` `NEQ` `LT` `LTE` `GT` `GTE` `IN` `NOT_IN` `CONTAINS` `NOT_CONTAINS` `EXISTS` `NOT_EXISTS`

### Expression Tree (7 Types)

#### RuleLeaf
Single rule evaluation. Looks up `rule_id` in registry, evaluates `field operator value` against applicant data.

#### ANDNode
All children must PASS. NOT_APPLICABLE is filtered out before evaluation. FAIL > UNKNOWN > PASS.

#### ORNode
At least one child must PASS. NOT_APPLICABLE is filtered out. PASS > UNKNOWN > FAIL.

#### NOTNode
Inverts PASS ↔ FAIL. UNKNOWN and NOT_APPLICABLE stay unchanged.

#### ConditionalNode (IF/THEN)

| Condition | Consequence Evaluated? | Result |
|-----------|----------------------|--------|
| PASS | Yes | Consequence status |
| FAIL | No | NOT_APPLICABLE |
| UNKNOWN | No | UNKNOWN |
| NOT_APPLICABLE | No | NOT_APPLICABLE |

#### ExceptionNode (Rule UNLESS Exception)

| Exception | Main | Result |
|-----------|------|--------|
| PASS (applies) | PASS | FAIL (overridden) |
| PASS (applies) | FAIL | FAIL |
| PASS (applies) | UNKNOWN | UNKNOWN |
| FAIL/UNKNOWN/NOT_APPLICABLE | any | Main result unchanged |

#### DependencyNode (Rule REQUIRES Prerequisites)

| Dependency | Result |
|-----------|--------|
| FAIL | NOT_APPLICABLE |
| NOT_APPLICABLE | NOT_APPLICABLE |
| UNKNOWN | UNKNOWN |
| All PASS | Evaluate rule normally |

### Versioning

Rules have `version` (int) and `status` (DRAFT/VALIDATED/APPROVED/ACTIVE/SUPERSEDED/RETIRED). When policy changes:
1. Create v2 with ACTIVE status
2. Mark v1 as SUPERSEDED
3. Historical assessments still reference v1
4. Re-evaluating historical assessments against v1 produces identical results

Verified by `TestHistoricalAssessmentReproducibility` (3 tests).

---

## 4. Phase 3D — Government Knowledge Activation Pipeline

### Overview

Connects Phase 2 approved requirements to active, persistent rules in the database through a human-in-the-loop approval workflow.

### Pipeline Flow

```
Government Source
    │
    ▼ Phase 1 & 2
Approved Requirement (status=APPROVED)
    │
    ▼ compile_approved_requirements()
Rule Dict (compiled from requirement)
    │
    ▼ EnhancedRuleRepo.save()
Rule Version (status=DRAFT)
    │
    ▼ Admin: POST /validate
Rule Version (status=VALIDATED)
    │
    ▼ Admin: POST /approve
Rule Version (status=APPROVED)
    │
    ▼ Admin: POST /activate
Rule Version (status=ACTIVE)
    │
    ▼ (if v2) Transactional supersession
    │   v1 status: ACTIVE → SUPERSEDED
    │   v2 status: APPROVED → ACTIVE
```

### Status Machine

```
RuleLifecycleStatus:
    DRAFT → VALIDATED → APPROVED → ACTIVE → SUPERSEDED → RETIRED
                                   ↓
                               REJECTED (from any pre-active state)
```

**Illegal transitions raise `RuleActivationError`:**
- DRAFT → ACTIVE (bypasses VALIDATED/APPROVED)
- ACTIVE → DRAFT (no backwards)
- SUPERSEDED → ACTIVE (no resurrection)
- RETIRED → anything

### EnhancedRuleRepo

```python
class EnhancedRuleRepo:
    """Full CRUD + status transitions + transactional activation + version snapshots."""

    def save(self, rule_dict: dict, created_by: str) -> RuleVersion
    def get(self, rule_id: str) -> Rule | None
    def list(self, status: str | None = None) -> list[Rule]
    def activate(self, rule_id: str, rule_version_id: str, activated_by: str) -> None
    """Transactional: supersede v1 → activate v2. Never two ACTIVE for same logical key."""

    def get_active_version(self, rule_id: str) -> RuleVersion | None
    def get_all_versions(self, rule_id: str) -> list[RuleVersion]
```

**Version auto-increment**: `version_number` is computed as `max(existing version_numbers for this rule_id) + 1`, preventing UNIQUE constraint collisions on `rule_versions(rule_id, version_number)`.

### Requirement → Rule Bridge

```python
def compile_approved_requirements(session) -> list[dict]:
    """Load all approved requirements, compile each to a rule dict.
    Re-compiles existing ACTIVE requirements too (they get reset to DRAFT)."""

def _load_requirement_dict(req: Requirement) -> dict:
    """Load requirement with evidence into a compilation-ready dict."""
```

### Orchestrator

```python
sync_source(session, source_id, dry_run=False, trigger_type="ON_DEMAND") → SyncResult
# Creates PipelineRun at start, updates on completion/failure
# Steps: re-fetch (always) → classify fresh vs previous hash → compile rules
#   dry_run = stored-state preview via detect_change, no network

sync_all_sources(session, dry_run=False, trigger_type="ON_DEMAND") → [SyncResult]
# Syncs all ACTIVE sources, one PipelineRun per source
# SCHEDULED runs skip sources not yet due per crawl_frequency/last_checked_at;
# MANUAL/ON_DEMAND always re-fetch
```

Every sync execution tracked via `PipelineRun` (trigger_type: SCHEDULED/MANUAL/ON_DEMAND).

### Admin API

```python
# /api/v1/admin/rules
GET  /queue               → Review queue (DRAFT + VALIDATED + APPROVED rules)
GET  /                    → List all rules, optional ?status filter
GET  /{id}                → Get single rule with version history
POST /{id}/validate       → DRAFT → VALIDATED
POST /{id}/approve        → VALIDATED → APPROVED
POST /{id}/activate       → APPROVED → ACTIVE (transactional supersession)
POST /{id}/reject         → Any pre-active → REJECTED
POST /{id}/retire         → SUPERSEDED/RETIRED → RETIRED
POST /batch/activate      → Bulk activate multiple rules
```

### Pipeline Run API

```python
# /api/v1/admin/runs
GET  /                    → List all runs (newest first, paginated)
GET  /summary             → Aggregate counts by status
GET  /{id}                → Get single run details
GET  /source/{id}         → List runs for a specific source
```

### PipelineRun Model

```python
PipelineRun:
    run_id: UUID
    source_id: FK → GovernmentSource
    trigger_type: str          # SCHEDULED / MANUAL / ON_DEMAND
    status: str                # RUNNING / COMPLETED / FAILED / PARTIAL
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    old_hash: str | None       # content hash before sync
    new_hash: str | None       # content hash after sync
    requirements_extracted: int
    requirements_approved: int
    rules_created: int
    rules_validated: int       # computed at query time (not stored)
    rules_approved: int        # computed at query time (not stored)
    rules_activated: int       # computed at query time (not stored)
    errors: JSON               # list of error strings
    metadata_json: JSON        # arbitrary metadata
```

**Computed counters**: `rules_validated`, `rules_approved`, `rules_activated` are calculated at query time via `_compute_rule_counts(session, source_id)` by counting Rule rows by status for the given source. These are not stored on the PipelineRun itself, as rules may be approved/activated after the run completes.

Non-admin users: 403.

### Provenance Chain (Complete)

```
RuleVersion.snapshot_json.requirement_id
    → Requirement (Phase 2 approved)
        → RequirementEvidence (extracted evidence)
            → DocumentChunk.text (verbatim source)
                → DocumentVersion (version number, hash)
                    → GovernmentDocument (filename, visa_category)
                        → GovernmentSource (URL, program, authority)
```

Every ACTIVE rule can answer: "Why does the system currently believe this rule exists?" with an exact chain from rule to government URL.

---

## 5. Phase 3C — Eligibility + Gap Engine

### Architecture

```
ACTIVE RULES (Phase 3D)
      +
APPLICANT PROFILE (user input)
      │
      ▼
APPLICABILITY FILTER
├── Program filter: rule.program_id == applicant.visa.program_id
├── Status filter: rule.status == ACTIVE
└── Version filter: highest ACTIVE version per rule_id
      │
      ▼
EXPRESSION TREE EVALUATION
├── Rule engine evaluates field/operator/value
├── AND/OR/NOT/IF_THEN/EXCEPTION/DEPENDENCY compose results
└── Four-state: PASS/FAIL/UNKNOWN/NOT_APPLICABLE
      │
      ▼
ELIGIBILITY ASSESSMENT
├── ELIGIBLE:    All applicable mandatory rules PASS
├── INELIGIBLE:  Any mandatory/critical rule FAIL
└── INCOMPLETE:  No FAIL, but some required information UNKNOWN
      │
      ▼ (if INCOMPLETE)
GAP ENGINE
├── Find all UNKNOWN evaluations
├── Resolve missing fields
├── Deduplicate (multiple rules → same field)
├── Prioritize: CRITICAL > HIGH > MEDIUM > LOW
├── Link to source rules and evidence
└── Generate deterministic questions
      │
      ▼
USER ANSWERS → NORMALIZE → RE-EVALUATE
```

### Applicant Profile Model

```python
ApplicantProfile(
    visa=VisaSelection(program_id="SKILLED_189"),
    age=30,                        # int | None
    english_level="proficient",    # str | None (none|vocational|competent|proficient|superior)
    points=70,                     # int | None
    occupation="261313",           # str | None (ANZSCO code)
    skills_assessment_status="VALID",  # str | None
    health_status="PASSED",        # str | None
    character_status="PASSED",     # str | None
    has_regional_nomination=None,  # bool | None
    visa_subclass=None,            # str | None
)
```

**Critical rule**: Missing fields are `None`, NOT default values. `to_rule_data()` excludes `None` fields from the dict. Rule engine returns `UNKNOWN` for any rule referencing a field not in the data.

### Applicability Filtering

```python
filter_applicable_rules(rules, applicant) → [Rule]
# Filters by: program_id match + ACTIVE status only

select_rule_version(rules, rule_id) → Rule | None
# Returns highest ACTIVE version
```

### Eligibility Assessment

```python
assess_eligibility(expression, rules_by_id, applicant) → EligibilityOutcome
# Evaluates expression tree against profile
# Maps raw statuses to business outcome

# Business outcomes:
# ELIGIBLE:   All PASS
# INELIGIBLE: Any FAIL
# INCOMPLETE: Some UNKNOWN, no FAIL
```

### Gap Engine

```python
GapEngine.find_gaps(evaluations, rules_by_id) → [Gap]

Gap:
    field: str                              # "points", "english_level", etc.
    priority: CRITICAL | HIGH | MEDIUM | LOW
    source_rule_ids: list[str]              # which rules need this field
    source_evidence: str | None             # government evidence
    question: str                           # user-facing question
```

Priority derivation:
- `critical=True` → CRITICAL
- `mandatory=True, critical=False` → HIGH
- `mandatory=False, critical=False` → MEDIUM

### Deterministic Questions

```python
QuestionBank.get_question("english_level") → QuestionDefinition
# text: "What is your current English proficiency level?"
# accepted_values: ["none", "vocational", "competent", "proficient", "superior"]
# validate(answer) → bool

AnswerNormalizer.normalize("english_level", "PROFICIENT") → "proficient"
AnswerNormalizer.normalize("english_level", "IELTS 7") → "competent"
```

---

## 6. API Layer

### Application Factory

```python
from api.main import create_app, app
# create_app() → FastAPI instance (used by uvicorn)
# app is the module-level singleton

# Lifespan handler:
#   - Sets up structured logging (INFO, JSON output)
#   - Initializes DB engine from DATABASE_URL
#   - Stores engine in app.state.db_engine
```

### CORS Configuration

```python
# main.py
_cors_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:3000")
_cors_origins = [o.strip() for o in _cors_origins_str.split(",") if o.strip()]
# Explicitly NOT allow_origins=["*"] — browser security risk with credentials
```

### Request Middleware

```python
# Every request gets:
#   - X-Request-ID (from header or auto-generated UUID)
#   - Structured logging context with request_id
#   - Response header X-Request-ID echoed back
```

### Route Registry

```
/health                              — GET health check (no auth)
/api/v1/auth/register                — POST (no auth)
/api/v1/auth/login                   — POST (no auth)
/api/v1/auth/me                      — GET (JWT required)
/api/v1/applicants/                  — POST, GET (JWT required)
/api/v1/applicants/{id}              — GET, PATCH (JWT required)
/api/v1/assessments/                 — POST, GET (JWT required)
/api/v1/assessments/{id}             — GET (JWT required)
/api/v1/assessments/evaluate         — POST (JWT required)
/api/v1/admin/rules/                 — GET (admin only)
/api/v1/admin/rules/queue            — GET (admin only)
/api/v1/admin/rules/{id}             — GET (admin only)
/api/v1/admin/rules/{id}/validate    — POST (admin only)
/api/v1/admin/rules/{id}/approve     — POST (admin only)
/api/v1/admin/rules/{id}/activate    — POST (admin only)
/api/v1/admin/rules/{id}/reject      — POST (admin only)
/api/v1/admin/rules/{id}/retire      — POST (admin only)
/api/v1/admin/requirements/          — GET (admin only)
/api/v1/admin/requirements/{id}      — GET (admin only)
/api/v1/admin/requirements/{id}/approve — POST (admin only)
/api/v1/admin/requirements/{id}/reject  — POST (admin only)
/api/v1/admin/sources/               — GET (admin only)
/api/v1/admin/sources/{id}           — GET (admin only)
/api/v1/admin/documents/             — GET (admin only)
/api/v1/admin/documents/{id}         — GET (admin only)
/api/v1/admin/documents/{id}/versions — GET (admin only)
/api/v1/admin/documents/{id}/chunks  — GET (admin only)
/api/v1/admin/documents/{id}/diff    — GET (admin only)
/api/v1/admin/knowledge-state/       — GET (admin only)
/api/v1/admin/runs/                  — GET (admin only)
/api/v1/admin/runs/summary           — GET (admin only)
/api/v1/admin/runs/{id}              — GET (admin only)
/api/v1/admin/runs/source/{id}       — GET (admin only)
/api/v1/admin/sync                   — POST (admin only)
```

### Error Responses

```json
{"detail": "User already exists"}       // 409 duplicate email
{"detail": "Not authenticated"}         // 401 no token
{"detail": "Not an admin"}              // 403 non-admin on admin route
{"detail": "Rule not found"}            // 404
{"detail": "Invalid status transition"} // 422 bad activation
```

---

## 7. Authentication

### JWT Flow

```
POST /register  →  bcrypt(password) → UserORM → 201
POST /login     →  verify(password) → jwt.encode({sub: user_id, role}) → 200
GET /me         →  Authorization: Bearer <token>
                    jwt.decode(token) → UserORM → 200
```

### Secret Management

```python
def _get_jwt_secret() -> str:
    """Fail-fast in production if JWT_SECRET_KEY not set."""
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        environment = os.getenv("ENVIRONMENT", "development")
        if environment == "production":
            raise ValueError(
                "JWT_SECRET_KEY environment variable is required in production. "
                "Set a strong, random secret in your environment."
            )
        logger.warning("JWT_SECRET_KEY not set, using development default")
        return "dev-secret-change-me"
    return secret
```

### Password Hashing

- bcrypt 4.2.1 (pinned to avoid passlib incompatibility)
- `passlib.context.CryptContext(schemes=["bcrypt"])`
- Duplicate email → 409 Conflict

---

## 8. Database

### ORM Models (25+ tables)

```
countries
immigration_programs
government_sources          (NOT NULL visa_category)
government_documents        (NOT NULL visa_category, raw_path, retrieved_at)
document_versions
document_chunks             (NOT NULL version_id FK)
fetch_log

requirements                (NOT NULL status, extracted_at)
requirement_evidence
requirement_versions
requirement_references
extraction_runs
requirement_review

rules
rule_versions               (UNIQUE rule_id + version_number)

applicant_profiles
assessments
assessment_rule_results
gaps

audit_log
llm_usage
llm_budget_log
llm_cache

pipeline_runs
```

### Database Abstraction

```python
# db/connection.py
get_engine(url) → Engine
get_session_factory(engine) → sessionmaker
session_scope(session_factory) → contextmanager
init_db(engine) → create_all()

# db/repositories.py — 20+ repository classes
# Each wraps a single table with typed CRUD operations
```

### PostgreSQL vs SQLite

| Feature | PostgreSQL (production) | SQLite (tests) |
|---------|------------------------|----------------|
| UUIDs | Native UUID type | `String(36)` |
| JSON | JSONB | JSON |
| Timestamps | TIMESTAMPTZ | DATETIME |
| Pool | StaticPool + async | StaticPool + check_same_thread=False |
| Migrations | Alembic | Manual |

### Indexes

```sql
idx_sources_program        ON government_sources(program_id)
idx_rule_versions_rule     ON rule_versions(rule_id)
uq_rule_version            ON rule_versions(rule_id, version_number)
uq_doc_version             ON document_versions(document_id, version_number)
idx_chunks_document        ON document_chunks(document_id, version_id)
idx_fetchlog_source        ON fetch_log(source_id, fetched_at)
```

---

## 9. LLM Governance

### Components

```
RateLimiter        — Per-model request throttling (sliding window)
RetryPolicy        — Exponential backoff with max delay cap
ResponseCache      — SHA256-keyed LRU cache with TTL
BudgetTracker      — Daily/monthly token budget limits
UsageLog           — Per-request cost tracking
LLMGovernance      — Facade orchestrating all components
```

### Cache Key Design

```python
cache_key = SHA256(
    content_hash + model_name + temperature + system_prompt_hash
)
```

### Usage Tracking

```python
request_id       → str (UUID)
model            → str ("gpt-4o")
input_tokens     → int
output_tokens    → int
estimated_cost   → float
cached           → bool
latency_ms       → int
created_at       → datetime
```

---

## 10. Structured Logging

### Features

- JSON output format for log aggregation
- Request-scoped context (request_id, user_id)
- PII redaction: date of birth, passport, email never logged
- Log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL

### PII Filters

```python
# Patterns automatically redacted from log output:
# - Date of birth (various formats)
# - Passport numbers
# - Email addresses
# - User IDs in sensitive contexts
```

---

## 11. Docker Deployment

### Dockerfile

```dockerfile
FROM python:3.12-slim
# Installs requirements, copies src/, runs uvicorn
# CMD: uvicorn src.api.main:create_app --factory --host 0.0.0.0 --port 8000
```

### docker-compose.yml

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: immigration
      POSTGRES_USER: immigration
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
  api:
    build: .
    depends_on: [db]
    environment:
      DATABASE_URL: postgresql://immigration:${DB_PASSWORD}@db:5432/immigration
      JWT_SECRET_KEY: ${JWT_SECRET_KEY}
      CORS_ORIGINS: ${CORS_ORIGINS}
    command: >
      sh -c "alembic upgrade head && uvicorn src.api.main:create_app --factory"
```

---

## 12. Data Model Summary

### Core Entity Chain

```
Government Source
    → Government Document
        → Document Version
            → Document Chunk
                → Requirement (+ Evidence)
                    → Rule Version (snapshot_json)
                        → Expression Tree
                            → Eligibility Assessment
                                → ELIGIBLE / INELIGIBLE / INCOMPLETE
                                    → Gap Engine → Questions
```

### Phase 1 Tables

`countries` `immigration_programs` `government_sources` `government_documents` `document_versions` `document_chunks` `fetch_log`

### Phase 2 Tables

`requirements` `requirement_evidence` `requirement_versions` `requirement_references` `extraction_runs` `requirement_review`

### Phase 3D Tables

`rules` `rule_versions` (with `snapshot_json` containing full compiled rule + evidence text)

### Phase 3C Models (in-memory)

`ApplicantProfile` `VisaSelection` `Rule` `Provenance` `RuleEvaluation` `EligibilityOutcome` `Gap` `QuestionDefinition`

---

## 13. Test Inventory

### Phase 3 Core (192 tests)

| File | Tests | Coverage |
|------|-------|----------|
| test_models.py | 8 | Rule/Provenance serialization, status values |
| test_operators.py | 27 | All 12 operators: PASS + FAIL + edge cases |
| test_rule_compiler.py | 8 | Phase 2 → Phase 3 compilation, rejection |
| test_rule_engine.py | 18 | Single rule evaluation, aggregate status |
| test_expressions.py | 33 | AND/OR/NOR truth tables, nesting |
| test_integration.py | 10 | Compile → evaluate → explain end-to-end |
| test_conditions.py | 31 | IF/THEN truth table, NOT_APPLICABLE in AND/OR |
| test_exceptions.py | 17 | Exception truth table, nesting, real cases |
| test_dependencies.py | 17 | Dependency truth table, chaining |
| test_versioning.py | 21 | Version selection, historical reproducibility |

### Phase 3C Eligibility (97 tests)

| File | Tests | Coverage |
|------|-------|----------|
| test_applicant.py | 22 | Profile construction, serialization, to_rule_data |
| test_applicability.py | 16 | Program/status/version filtering |
| test_assessment.py | 18 | ELIGIBLE/INELIGIBLE/INCOMPLETE, traces, counts |
| test_gap_engine.py | 14 | Gap detection, dedup, priority, linkage |
| test_questions.py | 27 | Question definitions, validation, normalization |

### Phase 3D Activation (134 tests)

| File | Tests | Coverage |
|------|-------|----------|
| test_rule_activation.py | 10 | Status machine, illegal transitions |
| test_rule_repository.py | 17 | EnhancedRuleRepo CRUD, versioning, activation |
| test_requirement_to_rule.py | 16 | Requirement→rule bridge, compile_approved_requirements |
| test_admin_rules_api.py | 12 | Admin API endpoints, auth, validation, activate |
| test_e2e_pipeline.py | 7 | Full pipeline: v1→ACTIVE→v2→SUPERSEDED |
| test_e2e_pipeline_lifecycle.py | 27 | Extended lifecycle scenarios |
| test_phase3d_hardening.py | 27 | One-active invariant, transactional activation, provenance, retirement, E2E lifecycle, failure safety, compilation safety |

### Phase 2 (61 tests)

| File | Tests | Coverage |
|------|-------|----------|
| test_schema.py | 11 | JSON Schema validation, logic rules |
| test_evidence.py | 8 | Exact substring verification, hallucination detection |
| test_normalization.py | 8 | Operator/unit/category normalization |
| test_deduplication.py | 8 | Semantic dedup, conflict detection |
| test_extraction.py | 9 | LLM contract tests, mock responses |
| test_integration.py | 4 | Full pipeline integration |
| test_gold_dataset.py | 3 | Gold standard evaluation |
| test_category_prompts.py | 10 | Deterministic visa_category → prompt selection (Phase 4) |
| test_category_gold_harness.py | 12 | Category-aware gold integrity: zero cross-category contamination, no fabricated evidence (Phase 3) |

### Infrastructure

| File | Tests | Coverage |
|------|-------|----------|
| test_db_models.py | 12 | ORM model constraints, relationships |
| test_repositories.py | 10 | Repository CRUD, query filters |
| test_logging.py | 17 | Structured logging, PII redaction |
| test_api.py | 38 | API routes: health, applicants, assessments (real), middleware, G9 regression, G12 FK fixes, admin-sync auth (401/403/200) |
| test_auth.py | 13 | JWT, register/login, bcrypt, duplicate email, role in token |
| test_llm_governance.py | 30 | Rate limiter, cache, budget, retry |
| test_security.py | 44 | JWT secrets, PII redaction, CORS, authorization, password security, token claims |
| test_scheduler.py | 28 | APScheduler start/stop, manual sync trigger, concurrent-sync lock |
| test_pipeline_runs.py | 42 | PipelineRun model, repo CRUD, API endpoints, computed counters, orchestrator integration |
| test_e2e_scenarios.py | 14 | Multi-program, multi-version, gap engine, evaluation, orchestration |

### Total: 899 collected — 897 passed / 2 skipped / 30 warnings, 0 failures (2026-09-15)

(The table above is a representative subset; the complete per-file inventory lives in the README test suite summary.)

---

## 14. Key Files

### Phase 3

- `src/phase3/models.py` — Rule, Provenance, RuleEvaluation, EligibilityResult
- `src/phase3/operators.py` — 12 declarative operators
- `src/phase3/expressions.py` — 7 expression types, recursive evaluator
- `src/phase3/rule_compiler.py` — Phase 2 requirement → Phase 3 rule
- `src/phase3/rule_engine.py` — Single rule + aggregate evaluation
- `src/phase3/explanations.py` — Human-readable evaluation explanations
- `src/phase3/rule_activation.py` — Status machine (DRAFT→ACTIVE lifecycle)
- `src/phase3/rule_repository.py` — EnhancedRuleRepo (CRUD, versioning, activation)
- `src/phase3/requirement_to_rule.py` — Approved requirement → rule compilation bridge

### Phase 3C

- `src/phase3c/applicant.py` — ApplicantProfile, VisaSelection
- `src/phase3c/applicability.py` — Program/status/version filtering
- `src/phase3c/assessment.py` — ELIGIBLE/INELIGIBLE/INCOMPLETE mapping
- `src/phase3c/gap_engine.py` — Missing field detection + prioritization
- `src/phase3c/questions.py` — Deterministic question templates + validation

### API & Auth

- `src/api/main.py` — FastAPI app factory, CORS, middleware, lifespan (TESTING guard)
- `src/api/helpers.py` — `parse_uuid_or_404()` utility
- `src/api/routes/auth.py` — Register/login/me
- `src/api/routes/applicants.py` — Applicant CRUD (JWT-protected)
- `src/api/routes/assessments.py` — Assessment create/list/evaluate
- `src/api/routes/admin_rules.py` — Admin review/approve/activate
- `src/api/routes/admin_requirements.py` — Admin requirement review (ORM-based)
- `src/api/routes/admin_sources.py` — Source admin API
- `src/api/routes/admin_documents.py` — Document admin API (G14): versions, chunks, diffs
- `src/api/routes/admin_knowledge.py` — Knowledge-state hierarchical endpoint (G15)
- `src/api/routes/pipeline_runs.py` — Pipeline run API with computed counters (G13)
- `src/services/auth_service.py` — JWT, bcrypt, secret management

### Infrastructure

- `src/db/models.py` — 25+ ORM models
- `src/db/connection.py` — Engine, session factory, session_scope
- `src/db/repositories.py` — 20+ repository classes
- `src/llm_governance.py` — RateLimiter, RetryPolicy, ResponseCache, BudgetTracker
- `src/logging_config.py` — Structured logging, PII redaction
- `src/orchestration/government_sync.py` — Source sync orchestrator (on-demand)

### Configuration

- `.env.example` — CORS_ORIGINS, JWT_SECRET_KEY, all config placeholders
- `alembic.ini` — Alembic migration config
- `Dockerfile` — Python 3.12-slim
- `docker-compose.yml` — postgres:16-alpine + api service
- `requirements.txt` — bcrypt==4.2.1 pinned
- `conftest.py` — Root conftest, adds `src/` to sys.path

### Baselines

- `validation/phase3_baseline_manifest.json` — Phase 3 frozen checkpoint
- `validation/run_report.json` — Phase 1 pipeline results

---

## 15. Design Principles

1. **Declarative rules only**: No lambdas, no dynamic code execution. Rules are data. Engine interprets generically.

2. **Evidence is verbatim**: Every extracted requirement must have evidence that is an exact substring of its source chunk.

3. **Missing = UNKNOWN, not FAIL**: `None` fields produce `UNKNOWN`, preserving the distinction between "we don't know" and "rule failed."

4. **No LLM in eligibility**: LLMs extract knowledge (Phase 2). Rules decide eligibility (Phase 3). RAG explains rules (future). None overlap.

5. **Historical integrity**: Rule v1 assessments stay v1. Government changes create v2. Historical users are never evaluated against current rules.

6. **Provenance preserved**: Every rule traces back through: requirement → chunk → document version → document → source → government URL.

7. **Four-state is necessary**: PASS/FAIL is insufficient. UNKNOWN ("we don't know your points") must not become FAIL ("you don't have enough points"). NOT_APPLICABLE ("189 rules don't apply to 190 applicants") must not pollute AND/OR.

8. **Gap priority from metadata**: CRITICAL/HIGH/MEDIUM/LOW comes from rule `critical`/`mandatory` flags, not arbitrary UI logic.

9. **Transactional activation**: Activating v2 atomically supersedes v1 for the same logical key. Never two ACTIVE for same field.

10. **Fail-fast in production**: JWT secret must be set. CORS wildcard is blocked. Database errors surface immediately.

---

## 16. Production Readiness Assessment

### Strengths

- 899 collected / 897 passing (2 skipped) with comprehensive coverage
- Strong domain separation (LLM extraction vs deterministic rules)
- Complete provenance chain from rule to government URL
- Structured logging with PII redaction
- JWT authentication with production secret fail-fast
- Versioned rule lifecycle with transactional activation
- Docker deployment with PostgreSQL
- LLM governance (rate limiting, caching, budget tracking)
- Real assessment API (ACTIVE rules → Phase 3C → persist)
- Automatic source sync scheduler (APScheduler)
- 40 security tests (auth, PII, CORS, passwords, authorization)
- PipelineRun audit trail for every sync execution (42 tests with computed counters)
- Admin documents API (versions, chunks, diffs)
- Admin knowledge-state hierarchical endpoint
- PipelineRun computed counters (rules validated/approved/activated at query time)
- Lifespan TESTING mode guard (G9 fix)
- **Real PostgreSQL ORM verification** (FK constraints, NOT NULL, full rule lifecycle)
- **Deterministic visa_category → prompt selection** in Phase 2 extraction (10 tests)
- **Category-aware gold scaffolding + integrity harness** (12 tests): per-category gold dirs under `data/gold/{skilled,work,student,visitor}/`, zero cross-category prompt contamination, no fabricated evidence

### Gaps

| Gap | Priority | Status | Description |
|-----|----------|--------|-------------|
| ~~Assessment API stubbed~~ | P0 | DONE | `POST /assessments` and `POST /assessments/evaluate` now real |
| ~~No scheduler~~ | P1 | DONE | APScheduler with configurable interval, manual trigger endpoint |
| ~~PostgreSQL verification~~ | P1 | DONE | ORM rule lifecycle + FK/NOT NULL verified against real PostgreSQL 2026-08-27 |
| ~~No PipelineRun tracking~~ | P2 | DONE | Full audit trail: model, repo, API, orchestrator integration |
| ~~No security test suite~~ | P2 | DONE | 44 tests covering auth, PII, CORS, passwords, authorization |
| ~~Admin documents API~~ | P2 | DONE | Document versions, chunks, diffs for admin visibility |
| ~~Admin knowledge-state endpoint~~ | P2 | DONE | Hierarchical country → category → program → sources tree |
| ~~G9 lifespan testing bug~~ | P2 | DONE | TESTING env var guard prevents DB init in test mode |
| ~~G10 UUID validation~~ | P2 | DONE | parse_uuid_or_404 helper on all UUID-based routes |
| ~~G13 computed PipelineRun counters~~ | P2 | DONE | rules_validated/approved/activated computed at query time |
| ~~G16/Phase 4 category prompts~~ | P2 | DONE | run_extraction.py derives visa_category → category prompt |
| ~~Admin-only enforcement on every admin endpoint~~ | P1 | DONE | 2026-09-15: `POST /api/v1/admin/sync` was unauthenticated — now gated via shared `require_admin`; all admin routes audited |
| ~~Playwright collection crash~~ | P1 | DONE | 2026-09-15: manual browser script moved out of `tests/` into `scripts/` — pytest no longer launches a browser at collection |
| WORK/STUDENT/VISITOR sources | P3 | DONE | 2026-09-15: registry extended to 14 sources across all four categories (482/500/600 mains + occupation list + CRICOS) |
| Live Home Affairs fetch | P3 | PARTIAL | Plain-HTTP 403 ACCESS_BLOCKED; browser path (headless=False) proven 200 — capture not yet operationalized |
| Real WORK/STUDENT/VISITOR gold data | P3 | OPEN | Gold dirs scaffolded with zero-item AWAITING_REAL_SOURCE placeholders (no fabricated evidence); skilled/ populated with real verbatim gold |
| Multi-country deferred | P3 | OPEN | Australia only, NZ/Canada/etc. later |
| 491 real-world coverage | P3 | OPEN | Partial real scenario validation |

### Verification performed (2026-08-27; re-verified 2026-09-15)

1. **899 collected / 897 passed / 2 skipped** on SQLite (0 failures, 30 SQLite datetime deprecation warnings).
2. **Real PostgreSQL**: `docker compose up -d postgres` + `alembic upgrade head`, then full ORM pipeline Country → Program → Source → Document → Version → Chunk → Requirement → Rule (DRAFT → VALIDATED → APPROVED → ACTIVE). FK constraints and NOT NULL enforced correctly.
3. **Live fetch**: Home Affairs returns 403 ACCESS_BLOCKED to the plain-HTTP fetcher — correctly classified, recorded, and source demoted (failure-safe by design); the Playwright browser path (`headless=False`) returns HTTP 200 on the same pages.
4. **CLI Phase 1/2**: offline pipeline + phase2 run_extraction against DOC-AU-189-MAIN extract 6 validated requirements with category-aware prompt wiring.
5. **Category-aware gold harness**: per-category dirs created; skilled gold (189 + 190, 29 items) migrated; work/student/visitor zero-item placeholders; integrity report clean (no contract violations, no cross-category leak).

### Next Milestones

1. **Fill real gold datasets** for WORK/STUDENT/VISITOR categories (sources now registered; dirs scaffolded: `data/gold/{skilled,work,student,visitor}/`)
2. **Operationalize live capture** of Home Affairs / CRICOS content via the browser path or official API
3. **Multi-country support** (NZ, Canada, UK)
4. **491 real-world scenario coverage**
5. **RAG & Recommendations** — LLM explains rules, suggests pathways
