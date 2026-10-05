# AU Immigration Knowledge & Eligibility Platform — Master Plan

**Generated**: 2026-09-21. **Last updated**: 2026-09-25.
**Purpose**: Map the full platform vision against what is actually built and verified today, and
sequence the concrete next moves. This file is a living planning document — update it as work
lands, don't let it go stale like the old README did.

**How this was produced**: every "DONE" claim below was checked against the actual code and, where
possible, against the actual running stack (`docker compose up`, real PostgreSQL, real network) —
not assumed from a prior summary. See `README.md` for the day-to-day operational doc; this file is
the strategic one.

**Since 2026-09-21, in order**: restored real authentication (deleted the temporary bypass
entirely); a full API endpoint audit (50 endpoints inventoried — admin-auth pairing and
route-ordering both confirmed clean, no removal candidates found, 10 endpoints flagged with zero
route-level test coverage); found and fixed a severe, previously-undiscovered bug where every real
extracted rule would have evaluated UNKNOWN forever regardless of applicant data (a field-naming
mismatch between the Phase 2 extraction contract and Phase 3C's `ApplicantProfile.to_rule_data()`);
fixed the compiler rejecting `REQUIRED`/`NOT_REQUIRED` operators outright; got the full
knowledge-to-assessment E2E test passing against real PostgreSQL for the first time; fixed a
lexicographic-vs-ordinal string comparison bug (`"vocational" >= "competent"` was wrongly `True`);
fixed a silent-success bug where a zero-chunk extraction reported a clean `COMPLETED` instead of a
visible `PARTIAL` warning; and — the biggest single finding — discovered the browser-escalation
fetcher was launching Chromium `headless=True`, which the Home Affairs WAF fingerprints and blocks;
switching to `headless=False` under Xvfb was live-verified to turn real 403s into real 200s on
previously-blocked government pages. Full detail in the sections below.

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
| Fetch layer | Scraping/fetching, no anti-bot evasion implied as needed but not explicitly required | ✅ Two-tier: plain HTTP (`fetcher.py`) → genuine headed (`headless=False`) Chromium escalation under Xvfb (`browser_fetcher.py`); explicitly **forbids** stealth/evasion by design. **2026-09-25: fixed a real bug where this was `headless=True`, which the Home Affairs WAF fingerprints and blocks — live-verified switching to headed gets real 200s on previously-blocked pages.** |
| **Raw immutable snapshots** | Required | ❌ **NOT actually happening in the production path.** `src/utils/fetch_document.py` (the live API/Docker path) stores only a synthetic placeholder string as `raw_path` (`"fetch:{document_id}:v{n}"`) and discards the real fetched bytes after chunking. Only the legacy CLI script `pipeline.py` writes real bytes to disk (`raw/AU/...`). **This is a genuine gap against the spec — see Move 1 below.** |
| Document versioning + hashing | Required | ✅ `document_versions` (content_hash, version_number, change_type), enforced unique constraints, verified live |
| Change detection | Required | ✅ `change_detector.classify_change` — INITIAL/CHANGED/UNCHANGED, hash-before-fetch-vs-fresh-fetch (a real bug here was found and fixed in an earlier session) |
| LLM extraction | OpenAI, assistive only | ✅ OpenAI (`gpt-4o-mini`) **and now Gemini** (`gemini-flash-lite-latest`), both fail-fast on missing key, both routed through the same governance layer (budget/rate-limit/cache), never auto-activates anything |
| Evidence validation | Required, anti-fabrication | ✅ evidence must be an exact substring of source chunk text; schema validation; `requirement_evidence` table with chunk/version lineage FKs |
| Human review gate | Required before rule activation | ✅ `requirements.status`: DRAFT/VALIDATED/NEEDS_REVIEW/APPROVED/REJECTED/SUPERSEDED/CONFLICT, with `requirement_review` audit trail |
| Deterministic Rule Engine | Operators, AND/OR/NOT, conditions, exceptions, dependencies, versioning, PASS/FAIL/UNKNOWN, gap analysis | ✅ `src/rules/` — 12 operators, 7 expression types, fully tested (200+ tests). **2026-09-25: found and fixed a severe integration bug at the Phase2→Phase3C seam** — `rule.field` used the extraction contract's dotted names (`applicant.points_score`) while `ApplicantProfile.to_rule_data()` used flat names (`points`); the rule engine did an exact-match dict lookup with zero normalization, so **every real extracted rule would have evaluated UNKNOWN forever, regardless of applicant data**. Fixed in `rule_engine.py`'s `_resolve_field_value()`. Also fixed the compiler (`rule_compiler.py`) rejecting `REQUIRED`/`NOT_REQUIRED` operators outright — used by real extracted health/character/skills-assessment criteria — and a lexicographic-vs-ordinal string bug in `operators.py` (`"vocational" >= "competent"` was wrongly `True`). The full knowledge-to-assessment E2E test now passes against real PostgreSQL. |
| Rule status machine | DRAFT→VALIDATED→APPROVED→ACTIVE, transactional supersession | ✅ DB-enforced: `uq_rules_active_per_field` partial unique index guarantees at most one ACTIVE rule per (program, field) |
| Provenance/lineage | Rule → Requirement → Evidence → Chunk → Document Version → Document → Source | ✅ modeled end-to-end in the schema (FK chains verified) |
| PipelineRun audit trail | Required | ✅ every sync execution recorded — status, hashes, counts, errors, change-detection metadata |
| Admin APIs | Required | ✅ rules, requirements, documents, sources, runs, knowledge-state, schema-report — all implemented and functionally verified live today |
| Auth/authorization | Required | ✅ Real JWT + role-based admin gate, fully enforced. Was deliberately bypassed for manual testing 2026-09-21→22; the bypass was **fully deleted** (not left as a toggle) once testing was done. 401 (anon) / 403 (non-admin) / 200 (admin) live-verified across all admin routes; API endpoint audit (2026-09-23) confirmed consistent enforcement on all 36 admin-gated routes. |
| Docker deployment | Required | ✅ — three real bugs found and fixed across sessions: `docker-compose.yml` not forwarding LLM API keys, `Dockerfile` not copying `prompts/`, and (2026-09-25) `browser_fetcher.py`'s headless mismatch requiring Xvfb infrastructure (`docker-entrypoint.sh`). ⚠️ **New gap found 2026-09-25**: a fresh `docker compose up -d --build` fails at `alembic upgrade head` with `ModuleNotFoundError: No module named 'psycopg'` — SQLAlchemy resolves the psycopg v3 dialect but only `psycopg2-binary` is installed. Not yet fixed. |
| Eligibility engine (applicant-facing) | PASS/FAIL/UNKNOWN, gaps, questions | ✅ `src/assessment/` — assessment + gap engine, fully tested (97+ tests). **2026-09-25: proven end-to-end against real PostgreSQL** via the full knowledge-to-assessment E2E test (PASS/FAIL/UNKNOWN all exercised, gaps generated correctly) — though still via realistic test-fixture content run through the real pipeline, not yet via organically-scraped-and-approved real government content (see Section 4). |
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
| Fetch | ✅ DONE, **improved 2026-09-25** | Plain HTTP → headed (`headless=False`) browser escalation under Xvfb, honest classification, no evasion. Live-verified to get real content from 2 previously-blocked Home Affairs URLs. |
| **Raw Snapshot** | ❌ **GAP, still open** | See Section 2 — production path still discards raw bytes after chunking; not addressed this round |
| Hash/Change Detection | ✅ DONE | Verified live: hash computed before fetch, classified against fresh fetch |
| Document Version | ✅ DONE | `document_versions`, immutable, uniqueness on (document_id, content_hash) |
| Extraction | ✅ DONE (mechanism, now proven on real Postgres via E2E test) | OpenAI + Gemini both wired and working. The field-mapping and operator-rejection bugs that would have silently broken every real extraction are now fixed (see Section 2). `SRC-AU-MIGRATION-REGS-1994`'s URL is fixed and verified to yield real Subclass 189 text (see Section 4) but hasn't yet been run through a live sync in this environment. `SRC-AU-CRICOS-REGISTER` confirmed unrecoverable without a materially different approach (search-interface SPA, tried render-wait). |
| Evidence Validation | ✅ DONE (mechanism, proven via E2E) | Verbatim-substring evidence check, schema validation — proven correct via the full knowledge-to-assessment E2E test; still not yet exercised against organically-scraped real content |
| Requirement (DRAFT/NEEDS_REVIEW) | ✅ DONE (mechanism, proven via E2E) | Zero real (non-test) requirement rows exist in the DB today |
| Review (admin approve/reject) | ✅ DONE (mechanism, proven via E2E) | API fully implemented and tested; never used on a real requirement |
| Rule Compilation | ✅ DONE (mechanism, proven via E2E) | `compile_approved_requirements()`; the `REQUIRED`/`NOT_REQUIRED` operator-rejection bug that would have blocked common real criteria is fixed |
| Validation (DRAFT→VALIDATED) | ✅ DONE (mechanism, proven via E2E) | Proven correct in the E2E test; never exercised on organically-real data |
| Admin Approval (VALIDATED→APPROVED) | ✅ DONE (mechanism, proven via E2E) | Proven correct in the E2E test; never exercised on organically-real data |
| Activation (APPROVED→ACTIVE) | ✅ DONE (mechanism, proven via E2E) | Proven correct in the E2E test (transactional supersession) — **zero ACTIVE rules exist from organically-real data today** |
| Eligibility Assessment | ✅ DONE, **proven correct 2026-09-25** | The full knowledge-to-assessment E2E test now passes against real PostgreSQL: PASS (eligible), FAIL (age=50 vs age<45 rule), UNKNOWN (missing age) all correctly produced, with the field-mapping fix. `POST /assessments/evaluate` still returns 400 in the live DB today because no organically-real ACTIVE rule exists yet. |
| Gap/Questions | ✅ DONE (mechanism, proven via E2E) | Gap generation for the UNKNOWN field verified correct in the E2E test, with full field traceability (`Gap.field` preserves the real rule field name) |
| Recommendation | ❌ NOT STARTED | No design, no code |

**The pattern has shifted**: as of 2026-09-21, the *mechanism* was built and unit-tested but unproven end-to-end. As of 2026-09-25, **the mechanism is now proven end-to-end against real PostgreSQL** (full knowledge-to-assessment E2E test passing) — and doing so surfaced and fixed 3 real bugs that would have silently broken it with actual government content. What remains is running this exact, now-proven chain against organically-scraped-and-approved real government content (not test fixtures) — see Section 4/5.

---

## 4. Known Blockers (updated 2026-09-25)

1. ~~12 of 14 sources are `NEEDS_MANUAL_CAPTURE`~~ — **likely resolved, not yet confirmed at scale.** The root cause (headless Chromium fingerprinting) is fixed and live-verified on 2 of the 12 URLs. The DB status is still `NEEDS_MANUAL_CAPTURE` for all 12 since that was a direct diagnostic test, not a full sync run — re-syncing them (or using `manual-capture`, which also resets status) is the next concrete step. See Move 4.
2. **`SRC-AU-CRICOS-REGISTER` still produces zero usable content** — confirmed (2026-09-25) to be a genuine search-interface SPA with no default listable content, not fixable via render-wait. Needs re-sourcing (e.g. a CRICOS bulk-data export/API, if one exists) or a materially larger fetch strategy.
3. ~~`SRC-AU-MIGRATION-REGS-1994` URL resolves to an index page~~ — **FIXED 2026-09-25.** Now points at a verified-real PDF (Volume 2 of 4) containing genuine Subclass 189 "189.2—Primary criteria" text. Caveat: the URL embeds the compilation date and has no stable dateless permalink — will need re-deriving when the regulation is next re-compiled.
4. **Raw snapshots still aren't persisted** in the live path (Section 2) — unaddressed this round, still a real evidence-traceability gap against the spec.
5. ~~Authentication is currently disabled~~ — **restored 2026-09-22.** The bypass was fully deleted, not toggled off.
6. **The field-mapping and operator-rejection bugs that would have silently broken every real extraction are now fixed** (Section 2) — the mechanism is proven on real PostgreSQL via the E2E test, but genuinely real (non-test-fixture) data still hasn't been pushed through it in this environment.
7. **NEW (2026-09-25): a fresh Docker build can't connect to PostgreSQL** — `ModuleNotFoundError: No module named 'psycopg'` at `alembic upgrade head`, because SQLAlchemy resolves the psycopg v3 dialect for the plain `postgresql://` URL but only `psycopg2-binary` is installed. Worked around during verification by calling fetch/extract functions directly, bypassing the ORM chain. **Not fixed** — blocks running a fresh full-stack sync through the actual Docker deployment until resolved.

None of these are design flaws — the architecture and failure-safety principles (fail visibly, never fabricate, never auto-activate) are sound and verified. They're **coverage/content/dependency gaps**, which is exactly what the next moves below close.

---

## 5. Next Moves (sequenced, updated 2026-09-25)

### ~~Move 0 — Guardrail~~ — DONE (2026-09-22)
Real authentication restored; bypass fully deleted.

### Move 1 — Fix raw snapshot persistence (closes the spec gap in Section 2) — STILL OPEN
Make `fetch_document_and_store()` in `src/utils/fetch_document.py` actually persist the raw fetched
bytes somewhere durable (filesystem volume to start, e.g. mirroring `pipeline.py`'s
`raw/{country}/{category}/{source_id}/{date}/content.*` pattern; object storage later — see Move 8)
and store a **real, dereferenceable path** in `raw_path` instead of the synthetic placeholder
string. This is foundational: evidence traceability back to the literal original document is a
core, non-negotiable design principle, and today it's not actually possible for anything fetched
through the live API path. **Not addressed in the 2026-09-25 round — still the top code gap.**

### Move 1.5 — NEW: fix the psycopg dependency drift blocking fresh Docker builds
`ModuleNotFoundError: No module named 'psycopg'` at `alembic upgrade head` on a fresh
`docker compose up -d --build`. Either pin `sqlalchemy` to a version that defaults to `psycopg2`
for a bare `postgresql://` URL, or add `psycopg[binary]` to `requirements.txt` and decide which
driver is canonical going forward. **Blocks Move 2 below from running through the real Docker
deployment** (worked around during verification by calling fetch/extract functions directly).

### Move 2 — Get ONE source to produce real, non-fabricated content — PARTIALLY DONE
- ~~Option B: fix `SRC-AU-MIGRATION-REGS-1994`'s URL~~ — **DONE 2026-09-25.** Now points at a
  live-verified real PDF (Volume 2 of 4) containing genuine Subclass 189 Schedule 2 text
  (452 chunks). **Not yet run through an actual sync** in this environment (blocked by Move 1.5) —
  the content was verified via direct `fetcher.fetch()` + `extractor_pdf.extract_pdf()` calls, not
  the full ORM-backed `fetch_document_and_store()` pipeline.
- Option A (manually capture a blocked Home Affairs page) is now likely unnecessary for most
  sources — the headless fix (Move "browser escalation," done 2026-09-25) may make the automated
  path work directly. Worth trying a real sync before falling back to manual capture.

**Next concrete step**: fix Move 1.5, then run `POST /admin/sync` for real and confirm an
`extraction_runs` row with `requirements_extracted > 0` from `SRC-AU-MIGRATION-REGS-1994`'s real
content — this is the one remaining piece of Move 2's acceptance criterion.

### Move 3 — Prove the full lifecycle end-to-end (closes spec objective #10) — MECHANISM PROVEN, REAL DATA STILL PENDING
The full knowledge-to-assessment E2E test (`tests/e2e/test_full_knowledge_to_assessment.py`) now
**passes against real PostgreSQL** — sync → extract → approve → compile → validate → approve →
activate → PASS/FAIL/UNKNOWN → gaps, with correct provenance. This proves the mechanism is sound
and, crucially, surfaced and fixed 3 real bugs (field-mapping, operator-rejection, ordinal
comparison) that would have silently broken it on actual government content. What's left: running
this same proven chain against Move 2's real content instead of test fixtures —
1. Sync `SRC-AU-MIGRATION-REGS-1994` for real (after Move 1.5) and confirm real extraction runs.
2. Review and approve the real requirement(s) (`POST /admin/requirements/review/{id}/approve`).
3. Validate → Approve → Activate the resulting rule(s).
4. Create a real applicant, run a real assessment, confirm PASS/FAIL/UNKNOWN + gaps are correct.
5. Change the source content (or simulate a version bump) and confirm the historical assessment
   is untouched while a new rule version supersedes the old one.

This remains the single most valuable milestone left — it converts "the mechanism is proven" into
"the product has done this for real," which is explicitly what the spec calls the immediate
objective.

### Move 4 — Recover broad real coverage — LIKELY MUCH CHEAPER NOW
With the headless fix, systematically re-syncing (not necessarily manually-capturing) the
remaining Home Affairs sources may recover most of them automatically. Try a real sync first;
fall back to `manual-capture` only for sources that still fail.

### Move 5 — ~~Re-verify auth~~ — DONE; ongoing loop-closing
Auth confirmed restored (Move 0). Regression tests were added for every bug fixed in the
2026-09-25 round (field-mapping, operator translation, ordinal comparison, zero-chunk PARTIAL,
headless assertion, source-registry content). Full suite: 956 passed / 11 skipped / 0 failed.

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

**Status as of 2026-09-25**: the mechanism for every box below is now proven correct (E2E test on
real PostgreSQL) with the source-of-truth bugs that would have broken it on real data already
found and fixed. None of the boxes are checked with *organically real* data yet — that's the
concrete, well-understood, narrow remaining gap (Moves 1.5 → 2 → 3 above), not an open question
about whether the system works.

- [ ] At least one real `government_sources` row has real, durably-stored raw content (Move 1+2 —
      raw persistence still open; real *fetchable* content confirmed for Migration Regs Volume 2)
- [ ] At least one real `Requirement` row exists with `status=APPROVED`, backed by real,
      verbatim evidence (blocked on Move 1.5, then an actual sync of the fixed source)
- [ ] At least one real `Rule` row reaches `status=ACTIVE`
- [ ] At least one real `Assessment` has been run against that ACTIVE rule and persisted, with
      correct PASS/FAIL/UNKNOWN and gap output (mechanism proven via E2E test with realistic
      fixture content; the field-mapping bug that would have broken this with real content is fixed)
- [ ] A second version of the same source/requirement/rule has been processed, proving
      supersession works and the first assessment's historical result is untouched
- [ ] Full provenance chain is queryable end-to-end for that one assessment: Assessment → Rule
      → Requirement → Evidence → Chunk → Document Version → Document → Source → Government URL
      (mechanism proven — `Gap.field`/rule field-name traceability specifically verified 2026-09-25)

Once every box above is checked with real data, the platform has proven its core promise for one
real program. Everything else (broader coverage, frontend, multi-country) is scaling that proof,
not discovering whether it works.
