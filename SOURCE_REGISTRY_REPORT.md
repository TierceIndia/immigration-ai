# Source Registry Report (G16)

Generated: **2026-09-15** · Original edition: 2026-08-27 · **Updated 2026-09-25** (see notes
inline — the biggest change: the browser-escalation path's headless/headed fetching bug is now
fixed, which materially changes the fetch-viability picture below).

## Executive Summary

The source registry contains **14 verified government sources** across **all four visa
categories — SKILLED, WORK, STUDENT, VISITOR** (7 programs total). Live **extraction** for the
non-SKILLED categories is still **NOT enabled**: their sources are registered and seed correctly,
but real gold evidence and live LLM extraction for them remain pending.

**2026-09-25 update — the fetch-viability picture changed materially**: `src/browser_fetcher.py`
was found to be launching Chromium `headless=True`, which the Home Affairs WAF fingerprints and
blocks (real `HTTP 403 even via genuine browser session`, affecting every `immi.homeaffairs.gov.au`
source below). Fixed to `headless=False` under Xvfb and **live-verified** to return real `HTTP 200`
with full real content on 2 of the previously-blocked URLs (189-MAIN, 190-MAIN) — the same URLs
that got 403 under headless days earlier. This has **not yet been confirmed across all affected
sources via an actual sync run** (see Section 8) — that's the immediate next validation step.
Separately, `SRC-AU-MIGRATION-REGS-1994`'s URL was found to be an interactive index/citation page
with no static text and has been corrected to a verified-real PDF deep-link (see Section 2.4).

---

## 1. Current Coverage

### By Visa Category

| Category | Sources | Programs | Status |
|----------|---------|----------|--------|
| SKILLED | 9 | 4 | COMPLETE (incl. governing legislation) |
| WORK | 2 | 1 | BASE — main page + occupation-list reference added |
| STUDENT | 2 | 1 | BASE — main page + CRICOS register added |
| VISITOR | 1 | 1 | BASE — main page only (no reference-data page exists) |
| **Total** | **14** | **7** | — |

### By Program

| Program ID | Subclass | Name | Sources | Source Types |
|------------|----------|------|---------|--------------|
| SKILLED_189 | 189 | Skilled Independent | 4 | Main page, Points stream, Points table, SkillSelect EOI |
| SKILLED_190 | 190 | Skilled Nominated | 2 | Main page, Points table |
| SKILLED_491 | 491 | Skilled Work Regional | 2 | Main page, Points table |
| LEGISLATION_MIGRATION_REGS | — | Migration Regulations 1994 | 1 | Legislation text |
| WORK_482 | 482 | Skills in Demand (TSS) | 2 | Main page, Skill Occupation List (shared reference) |
| STUDENT_500 | 500 | Student | 2 | Main page, CRICOS register (shared reference) |
| VISITOR_600 | 600 | Visitor | 1 | Main page |

### By Source Type

| Source Type | Count | Description |
|-------------|-------|-------------|
| PROGRAM_OVERVIEW | 6 | Main visa listing pages (189, 190, 491, 482, 500, 600) |
| POINTS_TABLE | 3 | Points tables (189, 190, 491) |
| ELIGIBILITY_GUIDANCE | 1 | 189 points-tested stream |
| PROCESS_GUIDANCE | 1 | SkillSelect EOI process (shared 189/190/491) |
| LEGISLATION_TEXT | 1 | Migration Regulations 1994 (governs all subclasses) |
| REFERENCE_DATA | 2 | Skill Occupation List + CRICOS register |

### By Authority

| Authority | Sources |
|-----------|---------|
| Department of Home Affairs | 12 |
| Federal Register of Legislation | 1 |
| Department of Education (CRICOS register) | 1 |
| **Total** | **14** |

### By Priority

| Priority | Sources |
|----------|---------|
| PRIMARY | 11 |
| SECONDARY | 3 (SkillSelect EOI, Skill Occupation List, CRICOS) |
| **Total** | **14** |

### By Crawl Frequency

| Frequency | Sources |
|-----------|---------|
| DAILY | 7 (main pages: 189, 189 points-tested, 190, 491, 482, 500, 600) |
| WEEKLY | 7 (points tables, SkillSelect EOI, legislation, occupation list, CRICOS) |
| **Total** | **14** |

---

## 2. Detailed Source Inventory

### 2.1 Skilled Independent (189)

| Source ID | Type | Subtype | URL | Priority | Frequency |
|-----------|------|---------|-----|----------|-----------|
| SRC-AU-189-MAIN | HTML | PROGRAM_OVERVIEW | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189 | PRIMARY | DAILY |
| SRC-AU-189-POINTS-STREAM | HTML | ELIGIBILITY_GUIDANCE | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189/points-tested | PRIMARY | DAILY |
| SRC-AU-189-POINTS-TABLE | HTML | POINTS_TABLE | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189/points-table | PRIMARY | WEEKLY |
| SRC-AU-SKILLSELECT-EOI | HTML | PROCESS_GUIDANCE | https://immi.homeaffairs.gov.au/visas/working-in-australia/skillselect/expression-of-interest | SECONDARY | WEEKLY |

**Notes**: SkillSelect EOI page is shared across 189/190/491. Linked to SKILLED_189 as primary program for FK purposes.

### 2.2 Skilled Nominated (190)

| Source ID | Type | Subtype | URL | Priority | Frequency |
|-----------|------|---------|-----|----------|-----------|
| SRC-AU-190-MAIN | HTML | PROGRAM_OVERVIEW | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-nominated-190 | PRIMARY | DAILY |
| SRC-AU-190-POINTS-TABLE | HTML | POINTS_TABLE | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-nominated-190/points-table | PRIMARY | WEEKLY |

**Gap**: No separate eligibility guidance or points-tested stream page for 190.

### 2.3 Skilled Work Regional (491)

| Source ID | Type | Subtype | URL | Priority | Frequency |
|-----------|------|---------|-----|----------|-----------|
| SRC-AU-491-MAIN | HTML | PROGRAM_OVERVIEW | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-work-regional-provisional-491 | PRIMARY | DAILY |
| SRC-AU-491-POINTS-TABLE | HTML | POINTS_TABLE | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-work-regional-provisional-491/points-table | PRIMARY | WEEKLY |

**Gap**: No separate eligibility guidance or points-tested stream page for 491.

### 2.4 Legislation

| Source ID | Type | Subtype | URL | Priority | Frequency |
|-----------|------|---------|-----|----------|-----------|
| SRC-AU-MIGRATION-REGS-1994 | **PDF** (was HTML) | LEGISLATION_TEXT | `https://www.legislation.gov.au/F1996B03551/2026-07-01/2026-07-01/text/original/pdf/2` (was `.../latest/text`) | PRIMARY | WEEKLY |

**Note**: Highest-authority source (binding regulation). Governs Schedule 2 criteria for subclasses 189/190/491 among others.

**2026-09-25 fix**: the previous URL (`/latest/text`) is a Federal Register of Legislation
interactive expand-tree UI with no static text — confirmed even with a headed-browser
`networkidle` render-wait (8 chunks, ~400 chars, all navigation/citation metadata). The regulation
is split across 4 PDF volumes; **Volume 2 of 4** was live-verified to contain the real "Subclass
189—Skilled—Independent... 189.2—Primary criteria" text (452 real chunks) — Volume 1 is just Part 1
"Preliminary" definitions, Volume 2 covers "Schedule 2, Subclasses 010 to 801" (which includes
189/190/491/482/500/600), Volume 3 covers Subclasses 802–995, Volume 4 is endnotes. **Caveat**: this
URL embeds the compilation date (`2026-07-01` twice) and the site does not appear to offer a stable
dateless permalink to a volume's text — the download links themselves are JS-rendered (not plain
`<a href>`), so re-deriving the URL after the next re-compilation requires a real browser fetch of
`/F1996B03551/latest/downloads` and reading the new dated href for Volume 2.

### 2.5 Work (482) — added 2026-09-15

| Source ID | Type | Subtype | URL | Priority | Frequency |
|-----------|------|---------|-----|----------|-----------|
| SRC-AU-482-MAIN | HTML | PROGRAM_OVERVIEW | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skills-in-demand-visa-subclass-482 | PRIMARY | DAILY |
| SRC-AU-SKILL-OCCUPATION-LIST | HTML | REFERENCE_DATA | https://immi.homeaffairs.gov.au/visas/working-in-australia/skill-occupation-list | SECONDARY | WEEKLY |

**Notes**: The 482 visa was renamed to the "Skills in Demand" visa; the page URL above is the current official listing. The Skill Occupation List is a shared reference register (also relevant to skilled employer pathways); it is not a per-program criteria page.

### 2.6 Student (500) — added 2026-09-15

| Source ID | Type | Subtype | URL | Priority | Frequency |
|-----------|------|---------|-----|----------|-----------|
| SRC-AU-500-MAIN | HTML | PROGRAM_OVERVIEW | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/student-500 | PRIMARY | DAILY |
| SRC-AU-CRICOS-REGISTER | HTML | REFERENCE_DATA | https://cricos.education.gov.au | SECONDARY | WEEKLY |

**Notes**: CRICOS is the official register of registered education providers and courses. Student eligibility depends on enrolment at a CRICOS-registered provider; there is no "occupation list" equivalent for the Student visa — the course/provider register plays that role.

### 2.7 Visitor (600) — added 2026-09-15

| Source ID | Type | Subtype | URL | Priority | Frequency |
|-----------|------|---------|-----|----------|-----------|
| SRC-AU-600-MAIN | HTML | PROGRAM_OVERVIEW | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/visitor-600 | PRIMARY | DAILY |

**Notes**: Visitor visas have no occupation/course-registration concept, so there is no dedicated reference-data page. Only the main overview page is tracked for now.

---

## 3. Remaining Coverage Gaps — WORK Category

Registered: **482 main + Skill Occupation List**. Still missing related work visas:

| Visa | Type | Key Government URL |
|------|------|-------------------|
| 494 | Skilled Employer Sponsored Regional | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-employer-sponsored-regional-494 |
| 186 | Employer Nomination Scheme | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/employer-nomination-scheme-186 |
| 187 | Regional Sponsored Migration Scheme | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/regional-sponsored-migration-scheme-187 |
| 407 | Training | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/training-407 |
| 408 | Temporary Activity | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/temporary-activity-408 |

**Recommendation**: Next work priority is 186 (ENS) and 494 (SESR) — both employer-sponsored like 482.

---

## 4. Remaining Coverage Gaps — STUDENT Category

Registered: **500 main + CRICOS register**. Still missing related student visas:

| Visa | Type | Key Government URL |
|------|------|-------------------|
| 590 | Student Guardian | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/student-guardian-590 |
| 485 | Temporary Graduate | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/temporary-graduate-485 |

**Recommendation**: Next student priority is 485 (Temporary Graduate).

---

## 5. Remaining Coverage Gaps — VISITOR Category

Registered: **600 main only**. Still missing related visitor visas:

| Visa | Type | Key Government URL |
|------|------|-------------------|
| 601 | Electronic Travel Authority | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601 |
| 651 | eVisitor | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/evisitor-651 |

---

## 6. Source Quality Assessment

### Strengths

1. **Legislation coverage**: Migration Regulations 1994 is the single highest-authority source, governing all subclasses. Already registered and verified.
2. **Points test coverage**: All three skilled subclasses (189/190/491) have points table sources.
3. **Main page coverage**: All four visa categories now have at least one main overview page (189, 190, 491, 482, 500, 600).
4. **Shared reference registers**: Skill Occupation List (482/work) and CRICOS (500/student) registered with clear notes that they are reference data, not per-program criteria.
5. **Crawl frequencies appropriate**: Main pages daily (high change risk), legislation/tables/reference pages weekly (lower change risk).

### Known Limitations

1. **Non-SKILLED live extraction NOT enabled**: WORK / STUDENT / VISITOR sources seed and sync-manage correctly, but no real gold evidence or approved requirements exist for them yet — eligibility assessment for these categories is not live.
2. **Web/network constraints — improved 2026-09-25**: Plain-HTTP fetch of `immi.homeaffairs.gov.au` still returns HTTP 403 ACCESS_BLOCKED (WAF), as expected — this is what the browser-escalation path exists for. The browser path (`src/browser_fetcher.py`) was found to be launching Chromium `headless=True`, which the WAF *also* blocks with a real 403; fixed to `headless=False` under Xvfb and live-verified to return real HTTP 200 with full content on 2 sample URLs. Not yet confirmed via an actual sync across all 12 previously-blocked sources — see the main README's Current Gaps table for the concrete next step (re-sync and confirm).
3. **Incomplete 190/491 coverage**: Missing eligibility guidance pages (only main + points table).
4. **No state/territory nomination sources**: 190 requires state/territory nomination — no sources for nomination criteria.
5. **No skills assessment sources**: Required for most skilled visas — no sources for assessing-authority pages.
6. **No health/character requirement sources**: These apply across all visa types — no dedicated sources.

---

## 7. Remaining Source Registry Expansion Plan

### Phase A: WORK Category (next priority)

```
SRC-AU-186-MAIN         → https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/employer-nomination-scheme-186
SRC-AU-494-MAIN         → https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-employer-sponsored-regional-494
```

### Phase B: STUDENT Category

```
SRC-AU-485-MAIN         → https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/temporary-graduate-485
```

### Phase C: VISITOR Category

```
SRC-AU-601-ETA          → https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601
SRC-AU-651-EVISITOR     → https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/evisitor-651
```

### Phase D: Supporting Sources

```
SRC-AU-SKILLS-ASSESSMENT → https://immi.homeaffairs.gov.au/skilled/skills-assessment
SRC-AU-190-NOMINATION    → (state/territory specific — multiple sources)
```

---

## 8. Current Extraction Run Status

**This section is intentionally not a static table** — extraction/sync history is runtime state
that resets whenever the local dev PostgreSQL volume is recreated (which has happened multiple
times across this project's sessions), so a hardcoded snapshot here goes stale and misleading
almost immediately. Check live via `GET /api/v1/admin/runs/summary` (aggregate counts by status)
or `GET /api/v1/admin/runs/source/{source_id}` (per-source history), or query
`extraction_runs`/`pipeline_runs` directly.

**What's durably true regardless of environment resets** (mechanism-level facts, verified
2026-09-25):
- Real extraction against real content has been proven to work end-to-end on real PostgreSQL —
  see `tests/e2e/test_full_knowledge_to_assessment.py` and `PLAN.md` Section 3.
- `SRC-AU-MIGRATION-REGS-1994` now points at content confirmed (via direct fetch+parse, not yet a
  full sync) to yield 452 real chunks including genuine Subclass 189 criteria text.
- `SRC-AU-CRICOS-REGISTER` is confirmed to yield 0 chunks regardless of fetch strategy tried so
  far (plain HTTP, headed browser, headed browser + render-wait) — a real search-interface SPA,
  not a transient/environment issue.
- The 12 `immi.homeaffairs.gov.au` sources' automated-fetch viability improved materially with the
  2026-09-25 headless fix (see Section 6/Executive Summary) but that has not yet been confirmed via
  an actual `POST /admin/sync` run across all of them in this environment.

---

## 9. Summary

| Metric | Value |
|--------|-------|
| Total registered sources | 14 |
| Visa categories covered | 4 of 4 (SKILLED, WORK, STUDENT, VISITOR) |
| Programs covered | 7 (189, 190, 491, Legislation, 482, 500, 600) |
| Categories with live extraction | 1 of 4 (SKILLED only) |
| Remaining visa subclasses to add | 186, 494, 485, 590, 601, 651, 407, 408, 187 |
| Estimated sources needed for full AU coverage | ~25-30 |
| Next priority | Real gold + live extraction for WORK/STUDENT/VISITOR |