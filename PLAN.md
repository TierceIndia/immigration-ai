# AU Immigration Knowledge & Eligibility Platform — Master Plan

**Generated**: 2026-09-21
**Purpose**: Map the full platform vision against what is actually built and verified today, and
sequence the concrete next moves. This file is a living planning document — update it as work
lands, don't let it go stale like the old README did.

**How this was produced**: every "DONE" claim below was checked against the actual code and, where
possible, against the actual running stack (`docker compose up`, real PostgreSQL, real network) —
not assumed from a prior summary. See `README.md` for the day-to-day operational doc; this file is
the strategic one.

---

## 1. The Vision (condensed from the spec)

Build an AI-powered Immigration Consultancy Platform, Australia-first, that:

1. Discovers and registers official government immigration sources per country/category/program.
2. Continuously fetches those sources, stores **immutable raw snapshots**, versions them, hashes
   them, and detects content changes.
3. Extracts structured, evidence-linked **requirements** from government content (AI-assisted,
   never AI-authoritative).
4. Routes every extracted requirement through **validation → conflict detection → dedup → human
   review** before it can become an active rule.
5. Compiles approved requirements into **deterministic, versioned rules** — no hardcoded
   immigration logic.
6. Tracks source → document version → requirement → rule lineage forever, never silently
   corrupting or re-writing historical assessments.
7. Runs on a reliable, scheduled, failure-safe pipeline with full audit logging.
8. Serves applicants through an eligibility engine that produces PASS/FAIL/UNKNOWN, gap detection,
   deterministic follow-up questions, and eventually recommendations.
9. Is Australia-first (Skilled, Work, Student, Visitor, Family/Dependency) but architected to
   extend to Canada, UK, USA, New Zealand.
10. **Immediate objective**: prove the complete Australia lifecycle end-to-end — Official
    Government Source → Versioned Knowledge → Approved Rule → Active Rule → Applicant Assessment →
    Gap/Questions → Fully Traceable Historical Result — for at least one real program, with real
    (not fabricated) government content.

---

## 2. Architecture: Spec vs. Reality

| Component | Spec | Reality today |
|---|---|---|
| Backend API | FastAPI or NestJS | ✅ FastAPI, `src/api/` |
| Database | PostgreSQL | ✅ PostgreSQL 16 (prod), SQLite in-memory (tests) — 30 tables, real FK/unique/check constraints verified live |
| Scheduler | Reliable, scheduled, failure-safe | ✅ APScheduler, daily fixed-clock trigger (`GOVERNMENT_SYNC_HOUR_UTC`); ⚠️ never proven reliable against real government content over time — only run manually so far |
| Fetch layer | Scraping/fetching, no anti-bot evasion implied as needed but not explicitly required | ✅ Two-tier: plain HTTP (`fetcher.py`) → genuine non-headless Chromium escalation (`browser_fetcher.py`); explicitly **forbids** stealth/evasion by design |
| **Raw immutable snapshots** | Required | ❌ **NOT actually happening in the production path.** `src/utils/fetch_document.py` (the live API/Docker path) stores only a synthetic placeholder string as `raw_path` (`"fetch:{document_id}:v{n}"`) and discards the real fetched bytes after chunking. Only the legacy CLI script `pipeline.py` writes real bytes to disk (`raw/AU/...`). **This is a genuine gap against the spec — see Move 1 below.** |
| Document versioning + hashing | Required | ✅ `document_versions` (content_hash, version_number, change_type), enforced unique constraints, verified live |
| Change detection | Required | ✅ `change_detector.classify_change` — INITIAL/CHANGED/UNCHANGED, hash-before-fetch-vs-fresh-fetch (a real bug here was found and fixed in an earlier session) |
| LLM extraction | OpenAI, assistive only | ✅ OpenAI (`gpt-4o-mini`) **and now Gemini** (`gemini-flash-lite-latest`), both fail-fast on missing key, both routed through the same governance layer (budget/rate-limit/cache), never auto-activates anything |
| Evidence validation | Required, anti-fabrication | ✅ evidence must be an exact substring of source chunk text; schema validation; `requirement_evidence` table with chunk/version lineage FKs |
| Human review gate | Required before rule activation | ✅ `requirements.status`: DRAFT/VALIDATED/NEEDS_REVIEW/APPROVED/REJECTED/SUPERSEDED/CONFLICT, with `requirement_review` audit trail |
| Deterministic Rule Engine | Operators, AND/OR/NOT, conditions, exceptions, dependencies, versioning, PASS/FAIL/UNKNOWN, gap analysis | ✅ `src/phase3/` — 12 operators, 7 expression types, fully tested (192 tests) — **but never yet run against a single piece of real extracted government content, only synthetic/test rules** |
| Rule status machine | DRAFT→VALIDATED→APPROVED→ACTIVE, transactional supersession | ✅ DB-enforced: `uq_rules_active_per_field` partial unique index guarantees at most one ACTIVE rule per (program, field) |
| Provenance/lineage | Rule → Requirement → Evidence → Chunk → Document Version → Document → Source | ✅ modeled end-to-end in the schema (FK chains verified) |
| PipelineRun audit trail | Required | ✅ every sync execution recorded — status, hashes, counts, errors, change-detection metadata |
| Admin APIs | Required | ✅ rules, requirements, documents, sources, runs, knowledge-state, schema-report — all implemented and functionally verified live today |
| Auth/authorization | Required | ⚠️ **Implemented (JWT + role-based admin gate) but currently deliberately disabled** for manual testing — see README banner. Must be restored (Move 5). |
| Docker deployment | Required | ✅ now — **two critical bugs fixed today**: `docker-compose.yml` never forwarded LLM API keys into the container, and `Dockerfile` never copied `prompts/` into the image. Before today's fixes, the deployed container could never have performed real extraction with any provider. |
| Eligibility engine (applicant-facing) | PASS/FAIL/UNKNOWN, gaps, questions | ✅ `src/phase3c/` — assessment + gap engine, fully tested (97 tests) — **never yet run against a real applicant + real ACTIVE rule**, only synthetic fixtures |
| Recommendation layer | Implied ("Immigration Recommendation") | ❌ NOT STARTED — no code, no design |
| Next.js frontend | Required | ❌ NOT STARTED — zero frontend code anywhere in the repo; API-only today |
| Redis | Required (spec architecture) | ❌ NOT STARTED — no Redis client, no caching/session layer beyond in-process governance cache |
| Object storage (S3-class) | Implied by "immutable raw snapshots" | ❌ NOT STARTED — no blob storage integration at all; ties directly to the raw-snapshot gap above |
| LangGraph orchestration | "where orchestration is genuinely required" | ❌ NOT USED — current orchestration is a straightforward hand-rolled Python pipeline (`government_sync.py`). Arguably **correct** for now: the pipeline is linear, not agentic, so LangGraph would add complexity without benefit yet. Revisit only if a genuinely multi-branch/agentic step emerges. |
| Notifications | Implied ("admin review and notifications") | ❌ NOT STARTED — no email/webhook/notification mechanism; review queue is pull-only (an admin must poll `GET /admin/rules/queue`) |
| Multi-country extensibility | Architecture should allow it later | ⚠️ Schema has `country_id` everywhere so it's *not blocked*, but nothing has been tested with a second country, and some code/docs assume AU implicitly |
| Family/Dependency visa category | Listed as in-scope | ❌ NOT STARTED — only SKILLED/WORK/STUDENT/VISITOR exist in the source registry (14 sources, 7 programs) |

---

## 3. Pipeline Stage-by-Stage Status

`Source Registry → Scheduler → Fetch → Raw Snapshot → Hash/Change Detection → Document Version →
Extraction → Evidence Validation → Requirement → Review → Rule Compilation → Validation → Admin
Approval → Activation → Eligibility Assessment → Gap/Questions → Recommendation`

| Stage | Status | Detail |
|---|---|---|
| Source Registry | ✅ DONE | 14 sources seeded, SKILLED(9)/WORK(2)/STUDENT(2)/VISITOR(1), DB-enforced URL uniqueness |
| Scheduler | ✅ DONE (mechanism) / ⚠️ UNPROVEN (reliability) | APScheduler wired; only ever run manually/on-demand so far in this environment |
| Fetch | ✅ DONE | Plain HTTP → browser escalation, honest classification, no evasion |
| **Raw Snapshot** | ❌ **GAP** | See Section 2 — production path discards raw bytes after chunking |
| Hash/Change Detection | ✅ DONE | Verified live: hash computed before fetch, classified against fresh fetch |
| Document Version | ✅ DONE | `document_versions`, immutable, uniqueness on (document_id, content_hash) |
| Extraction | ✅ DONE (mechanism) / ❌ **NOT PROVEN WITH REAL CONTENT** | OpenAI + Gemini both wired and working; the only two sources that ever reach extraction return **zero** real requirements (one is a JS-SPA shell with 0 parseable chunks, the other's URL points at an index page, not the actual legislation text) |
| Evidence Validation | ✅ DONE (mechanism) | Verbatim-substring evidence check, schema validation — untested against real content because none exists yet |
| Requirement (DRAFT/NEEDS_REVIEW) | ✅ DONE (mechanism) | Zero real requirement rows exist in the DB today |
| Review (admin approve/reject) | ✅ DONE (mechanism) | API fully implemented and tested; never used on a real requirement |
| Rule Compilation | ✅ DONE (mechanism) | `compile_approved_requirements()`; never run on real data |
| Validation (DRAFT→VALIDATED) | ✅ DONE (mechanism) | Never exercised on a real rule |
| Admin Approval (VALIDATED→APPROVED) | ✅ DONE (mechanism) | Never exercised on a real rule |
| Activation (APPROVED→ACTIVE) | ✅ DONE (mechanism) | Never exercised on a real rule — **zero ACTIVE rules exist in any program today** |
| Eligibility Assessment | ✅ DONE (mechanism) | Every `POST /assessments/evaluate` call today returns 400 "No ACTIVE rules found" because none exist |
| Gap/Questions | ✅ DONE (mechanism) | Fully implemented, tested with synthetic fixtures only |
| Recommendation | ❌ NOT STARTED | No design, no code |

**The pattern is consistent**: the *mechanism* for almost every pipeline stage is built and unit-tested. What's missing is **proof that the mechanism works end-to-end on one real piece of government content** — which is exactly the spec's stated "immediate objective" (#10).

---

## 4. Known Blockers (as of 2026-09-21)

1. **12 of 14 sources are `NEEDS_MANUAL_CAPTURE`** — genuine `HTTP 403` from `immi.homeaffairs.gov.au` even via a real, non-headless browser session. By design, the system does not fight this; it needs an admin to manually capture content via `POST /admin/sources/{id}/manual-capture`.
2. **The 2 remaining `ACTIVE` sources produce zero usable content**:
   - `SRC-AU-CRICOS-REGISTER` — likely a JS-rendered SPA; static fetch yields 0 document chunks.
   - `SRC-AU-MIGRATION-REGS-1994` — URL resolves to a Federal Register index/citation page, not the actual regulation text.
3. **Raw snapshots aren't actually persisted** in the live path (Section 2) — an evidence-traceability gap against the spec.
4. **Authentication is currently disabled** (deliberate, temporary, at the platform owner's request) — must be restored before any shared use.
5. **No real data has ever reached the rule engine** — the entire DRAFT→ACTIVE lifecycle and the eligibility engine are proven only against test fixtures, not real government content.

None of these are design flaws — the architecture and failure-safety principles (fail visibly, never fabricate, never auto-activate) are sound and verified. They're **coverage/content gaps**, which is exactly what the next moves below close.

---

## 5. Next Moves (sequenced)

### Move 0 — Guardrail (do this before anything else touches shared/public infrastructure)
Restore real authentication (`src/api/routes/auth.py` — delete the bypass, uncomment the original
`get_current_user`). Confirm `pytest tests/ -q` returns to 933 passed / 0 failed / 9 skipped.
*Do this the moment manual testing is done — don't let it linger.*

### Move 1 — Fix raw snapshot persistence (closes the spec gap in Section 2)
Make `fetch_document_and_store()` in `src/utils/fetch_document.py` actually persist the raw fetched
bytes somewhere durable (filesystem volume to start, e.g. mirroring `pipeline.py`'s
`raw/{country}/{category}/{source_id}/{date}/content.*` pattern; object storage later — see Move 8)
and store a **real, dereferenceable path** in `raw_path` instead of the synthetic placeholder
string. This is foundational: evidence traceability back to the literal original document is a
core, non-negotiable design principle, and today it's not actually possible for anything fetched
through the live API path.

### Move 2 — Get ONE source to produce real, non-fabricated content
Pick the path of least resistance:
- **Option A (fastest)**: manually capture one blocked Home Affairs page (e.g.
  `SRC-AU-189-MAIN`) via `POST /admin/sources/{id}/manual-capture` with hand-saved HTML.
- **Option B**: fix `SRC-AU-MIGRATION-REGS-1994`'s URL to point at the actual regulation text
  instead of the index page, and re-sync.

Either way, the acceptance criterion is: **at least one `extraction_runs` row with
`requirements_extracted > 0` from real government content**, not a test fixture.

### Move 3 — Prove the full lifecycle end-to-end (closes spec objective #10)
Using the real requirement(s) from Move 2:
1. Review and approve the requirement (`POST /admin/requirements/review/{id}/approve`).
2. Confirm it compiles to a DRAFT rule on the next sync.
3. Validate → Approve → Activate it (`POST /admin/rules/{id}/validate|approve|activate`).
4. Create a real applicant, run a real assessment (`POST /assessments`) against the newly
   ACTIVE rule, and confirm PASS/FAIL/UNKNOWN + gap/question output is correct.
5. Change the source content (or simulate a version bump) and confirm the historical assessment
   is untouched while a new rule version supersedes the old one.

This is the single most valuable milestone left — it converts "the mechanism works in tests" into
"the product works," and it's explicitly what the spec calls the immediate objective.

### Move 4 — Recover broad real coverage
Systematically manually-capture the remaining 11 blocked Home Affairs sources so SKILLED/WORK/
STUDENT/VISITOR each have real, current content — not just the one proof-of-concept source from
Move 2.

### Move 5 — Re-verify auth + close the loop on today's fixes
Confirm Move 0 landed, add the regression tests that make sense now that real content exists
(e.g., a real extraction → review → activation integration test), and get the full suite green.

### Move 6 — Minimal consultant/admin review workflow
The spec assumes an active human review step. Today that's API-only. Before investing in a full
frontend, decide: is a thin admin UI (even a simple internal tool) needed now, or is the existing
Admin API + a script/Postman collection sufficient for the current phase? Recommend the latter
until Move 3 is proven, then revisit.

### Move 7 — Expand category coverage
Add Family/Dependency sources and programs (currently entirely absent), and fill out 491
real-world coverage once the core lifecycle is proven repeatable.

### Move 8 — Architecture additions, only when justified
- **Object storage** (S3-class): once Move 1's filesystem-based raw storage proves the pattern,
  move it to real object storage for production durability/scaling.
- **Redis**: introduce only when a real need appears (e.g., cross-process rate limiting/budget
  state — today's `LLMGovernance` is in-memory per-process, which is fine for a single container
  but won't survive horizontal scaling).
- **LangGraph**: introduce only if a genuinely multi-branch/agentic orchestration need emerges.
  The current linear pipeline doesn't need it yet — adding it now would be complexity without
  benefit.

### Move 9 — Multi-country expansion
Only after the AU lifecycle is proven stable end-to-end (Move 3) and repeatable at scale
(Move 4/7). The schema already supports `country_id` throughout, so this should mostly be data
(new sources, new programs) plus verifying no AU-specific assumptions leaked into code.

### Move 10 — Frontend (Next.js)
Build the applicant-facing UI once the backend contract (assessment shape, gap/question shape,
auth flow) is stable and proven against real data — building a frontend against a backend that's
never seen real content risks designing against the wrong contract.

---

## 6. Definition of Done for the Immediate Objective (spec point #10)

- [ ] At least one real `government_sources` row has real, durably-stored raw content (Move 1+2)
- [ ] At least one real `Requirement` row exists with `status=APPROVED`, backed by real,
      verbatim evidence
- [ ] At least one real `Rule` row reaches `status=ACTIVE`
- [ ] At least one real `Assessment` has been run against that ACTIVE rule and persisted, with
      correct PASS/FAIL/UNKNOWN and gap output
- [ ] A second version of the same source/requirement/rule has been processed, proving
      supersession works and the first assessment's historical result is untouched
- [ ] Full provenance chain is queryable end-to-end for that one assessment: Assessment → Rule
      → Requirement → Evidence → Chunk → Document Version → Document → Source → Government URL

Once every box above is checked, the platform has proven its core promise for one real program.
Everything else (broader coverage, frontend, multi-country) is scaling that proof, not discovering
whether it works.
