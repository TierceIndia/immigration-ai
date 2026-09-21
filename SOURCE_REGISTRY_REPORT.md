# Source Registry Report (G16)

Generated: **2026-09-15** (updated) · Original edition: 2026-08-27

## Executive Summary

The source registry contains **14 verified government sources** across **all four visa
categories — SKILLED, WORK, STUDENT, VISITOR** (7 programs total). The 2026-09-15 update
added the first WORK / STUDENT / VISITOR sources (482 main + Skill Occupation List, 500 main +
CRICOS register, 600 main). Live **extraction** for the non-SKILLED categories is still **NOT
enabled**: their sources are registered and seed correctly, but real gold evidence and live
LLM extraction for them remain pending (plain-HTTP fetch is 403-blocked; the browser path has
been proven to return HTTP 200 but is not yet operationalized).

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
| SRC-AU-MIGRATION-REGS-1994 | HTML | LEGISLATION_TEXT | https://www.legislation.gov.au/F1996B03551/latest/text | PRIMARY | WEEKLY |

**Note**: Highest-authority source (binding regulation). Governs Schedule 2 criteria for subclasses 189/190/491 among others.

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
2. **Web/network constraints**: Plain-HTTP fetch of `immi.homeaffairs.gov.au` returns HTTP 403 ACCESS_BLOCKED (WAF). The browser path (`src/browser_fetcher.py`, headless=False) returns HTTP 200 but is not yet an operationalized capture pipeline.
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

The source registry seeds **14 sources**. The extraction pipeline has been exercised against the
SKILLED sources (test/offline fixtures, plus one real DOC-AU-189-MAIN offline extraction with
`MockLLMClient`). The WORK / STUDENT / VISITOR sources are registered but have **no extraction
runs yet** — a real OpenAI extraction against DOC-AU-189-MAIN is the next validation milestone.

| Source | Extraction Runs | Requirements Extracted | Rules Created |
|--------|----------------|----------------------|---------------|
| SRC-AU-189-MAIN | 1+ | Yes (23+ requirements) | Yes |
| SRC-AU-189-POINTS-STREAM | 0+ | Partial | Partial |
| SRC-AU-189-POINTS-TABLE | 0+ | Partial | Partial |
| SRC-AU-190-MAIN | 0+ | Partial | Partial |
| SRC-AU-190-POINTS-TABLE | 0+ | No | No |
| SRC-AU-491-MAIN | 0+ | Partial | Partial |
| SRC-AU-491-POINTS-TABLE | 0+ | No | No |
| SRC-AU-SKILLSELECT-EOI | 0+ | No | No |
| SRC-AU-MIGRATION-REGS-1994 | 0+ | No | No |
| SRC-AU-482-MAIN | — | — (registered, not yet extracted) | — |
| SRC-AU-SKILL-OCCUPATION-LIST | — | — (registered, not yet extracted) | — |
| SRC-AU-500-MAIN | — | — (registered, not yet extracted) | — |
| SRC-AU-CRICOS-REGISTER | — | — (registered, not yet extracted) | — |
| SRC-AU-600-MAIN | — | — (registered, not yet extracted) | — |

**Note**: Full extraction status depends on production runs; the above reflects test data only.

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