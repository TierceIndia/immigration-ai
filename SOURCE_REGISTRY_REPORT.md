# Source Registry Report (G16)

Generated: 2026-08-27

## Executive Summary

The source registry contains **9 verified government sources** across **4 immigration programs**, all within the **SKILLED** visa category. The **WORK**, **STUDENT**, and **VISITOR** categories have **zero sources** — the platform cannot currently assess eligibility for these visa types.

---

## 1. Current Coverage

### By Visa Category

| Category | Sources | Programs | Status |
|----------|---------|----------|--------|
| SKILLED | 9 | 4 | COMPLETE |
| WORK | 0 | 0 | NOT STARTED |
| STUDENT | 0 | 0 | NOT STARTED |
| VISITOR | 0 | 0 | NOT STARTED |
| **Total** | **9** | **4** | — |

### By Program

| Program ID | Subclass | Name | Sources | Source Types |
|------------|----------|------|---------|--------------|
| SKILLED_189 | 189 | Skilled Independent | 4 | Main page, Points stream, Points table, SkillSelect EOI |
| SKILLED_190 | 190 | Skilled Nominated | 2 | Main page, Points table |
| SKILLED_491 | 491 | Skilled Work Regional | 2 | Main page, Points table |
| LEGISLATION_MIGRATION_REGS | — | Migration Regulations 1994 | 1 | Legislation text |

### By Source Type

| Source Type | Count | Description |
|-------------|-------|-------------|
| PROGRAM_OVERVIEW | 3 | Main visa listing pages (189, 190, 491) |
| POINTS_TABLE | 3 | Points tables (189, 190, 491) |
| ELIGIBILITY_GUIDANCE | 1 | 189 points-tested stream |
| PROCESS_GUIDANCE | 1 | SkillSelect EOI process (shared 189/190/491) |
| LEGISLATION_TEXT | 1 | Migration Regulations 1994 (governs all subclasses) |
| **Total** | **9** | — |

### By Authority

| Authority | Sources |
|-----------|---------|
| Department of Home Affairs | 8 |
| Federal Register of Legislation | 1 |
| **Total** | **9** |

### By Priority

| Priority | Sources |
|----------|---------|
| PRIMARY | 8 |
| SECONDARY | 1 |
| **Total** | **9** |

### By Crawl Frequency

| Frequency | Sources |
|-----------|---------|
| DAILY | 4 (main pages for 189, 190, 491, and 189 points-tested) |
| WEEKLY | 5 (points tables, SkillSelect EOI, legislation) |
| **Total** | **9** |

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

---

## 3. Missing Sources — WORK Category

Australian work visas not covered:

| Visa | Type | Key Government URL |
|------|------|-------------------|
| 482 | Temporary Skill Shortage (TSS) | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/temporary-skill-shortage-482 |
| 494 | Skilled Employer Sponsored Regional | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-employer-sponsored-regional-494 |
| 186 | Employer Nomination Scheme | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/employer-nomination-scheme-186 |
| 187 | Regional Sponsored Migration Scheme | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/regional-sponsored-migration-scheme-187 |
| 407 | Training | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/training-407 |
| 408 | Temporary Activity | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/temporary-activity-408 |

**Recommendation**: Prioritize 482 (TSS) and 186 (ENS) as highest-demand work visas.

---

## 4. Missing Sources — STUDENT Category

| Visa | Type | Key Government URL |
|------|------|-------------------|
| 500 | Student | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/student-500 |
| 590 | Student Guardian | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/student-guardian-590 |
| 485 | Temporary Graduate | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/temporary-graduate-485 |

**Recommendation**: Prioritize 500 (Student) and 485 (Temporary Graduate) as highest-volume student visas.

---

## 5. Missing Sources — VISITOR Category

| Visa | Type | Key Government URL |
|------|------|-------------------|
| 600 | Visitor | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/visitor-600 |
| 601 | Electronic Travel Authority | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601 |
| 651 | eVisitor | https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/evisitor-651 |

**Recommendation**: Prioritize 600 (Visitor) as the most common visitor visa.

---

## 6. Source Quality Assessment

### Strengths

1. **Legislation coverage**: Migration Regulations 1994 is the single highest-authority source, governing all subclasses. Already registered and verified.
2. **Points test coverage**: All three skilled subclasses (189/190/491) have points table sources.
3. **Main page coverage**: All three skilled subclasses have main overview pages.
4. **Shared EOI process**: SkillSelect EOI page correctly shared across all three skilled subclasses.
5. **Crawl frequencies appropriate**: Main pages daily (high change risk), legislation and tables weekly (lower change risk).

### Gaps

1. **No WORK/STUDENT/VISITOR sources**: Platform cannot assess eligibility for ~70% of Australian visa types.
2. **Incomplete 190/491 coverage**: Missing eligibility guidance pages (only have main + points table).
3. **No state/territory nomination sources**: 190 requires state/territory nomination — no sources for nomination criteria.
4. **No occupation list sources**: Skilled visas require occupation on relevant list (MLTSSL, STSOL, ROL) — no sources registered.
5. **No skills assessment sources**: Required for most skilled visas — no sources for assessing authority pages.
6. **No health/character requirement sources**: These apply across all visa types — no dedicated sources.

---

## 7. Recommended Source Registry Expansion Plan

### Phase A: WORK Category (Priority 1)

```
SRC-AU-482-MAIN         → https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/temporary-skill-shortage-482
SRC-AU-482-ELIGIBILITY  → https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/temporary-skill-shortage-482/overview
SRC-AU-186-MAIN         → https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/employer-nomination-scheme-186
SRC-AU-186-ELIGIBILITY  → https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/employer-nomination-scheme-186/overview
SRC-AU-494-MAIN         → https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-employer-sponsored-regional-494
```

### Phase B: STUDENT Category (Priority 2)

```
SRC-AU-500-MAIN         → https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/student-500
SRC-AU-500-ELIGIBILITY  → https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/student-500/overview
SRC-AU-485-MAIN         → https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/temporary-graduate-485
```

### Phase C: VISITOR Category (Priority 3)

```
SRC-AU-600-MAIN         → https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/visitor-600
SRC-AU-600-ELIGIBILITY  → https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/visitor-600/overview
```

### Phase D: Supporting Sources (Priority 4)

```
SRC-AU-OCCUPATION-LISTS → https://immi.homeaffairs.gov.au/visas/working-in-australia/skillselect/occupation-lists
SRC-AU-SKILLS-ASSESSMENT → https://immi.homeaffairs.gov.au/skilled/skills-assessment
SRC-AU-190-NOMINATION    → (state/territory specific — multiple sources)
```

---

## 8. Current Extraction Run Status

The source registry seeds 9 sources. The actual extraction pipeline has been run against:

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

**Note**: Full extraction status depends on production runs; the above reflects test data only.

---

## 9. Summary

| Metric | Value |
|--------|-------|
| Total registered sources | 9 |
| Visa categories covered | 1 of 4 (SKILLED only) |
| Programs covered | 4 (189, 190, 491, Legislation) |
| Missing visa categories | WORK, STUDENT, VISITOR |
| Estimated sources needed for full AU coverage | ~25-30 |
| Priority expansion target | WORK category (482, 186, 494) |
