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
| 4+ | RAG, Recommendations, User Product | NOT STARTED |

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
   SQLITE METADATA STORE ── sources, documents, versions, chunks, fetch_log
```

No LLM. No rules. Pure data engineering.

### Source Registry

9 verified government sources across 3 visa programs plus governing legislation.

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
llm_client.py           — Mock (deterministic) or OpenAI/Gemini/Anthropic
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

Real gold dataset (`data/gold/requirements_gold_real.json`): 17 positive + 6 negative items for 189, 5+1 for 190. All 17 evidence texts verified as verbatim substrings.

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

Rules have `version` (int) and `status` (DRAFT/ACTIVE/SUPERSEDED/RETIRED). When policy changes:
1. Create v2 with ACTIVE status
2. Mark v1 as SUPERSEDED
3. Historical assessments still reference v1
4. Re-evaluating historical assessments against v1 produces identical results

Verified by `TestHistoricalAssessmentReproducibility` (3 tests).

---

## 4. Phase 3C — Eligibility + Gap Engine

### Architecture

```
APPROVED RULES (Phase 2)
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

## 5. Data Model Summary

### Core Entity Chain

```
Source → Document → Document Version → Document Chunk → Requirement → Rule
                                                                          │
                                                              ┌───────────┤
                                                              ▼           ▼
                                                     Expression Tree   Applicant
                                                     (AND/OR/NOT/     Profile
                                                      IF_THEN/         │
                                                      EXCEPTION/       ▼
                                                      DEPENDENCY)  Eligibility
                                                                    Assessment
                                                                          │
                                                                    ┌─────┴─────┐
                                                                    ▼           ▼
                                                              ELIGIBLE    INCOMPLETE
                                                              INELIGIBLE      │
                                                                    │         ▼
                                                                    │    Gap Engine
                                                                    │         │
                                                                    └────┬────┘
                                                                         ▼
                                                                   Final Result
```

### Phase 1 Tables

`countries` `immigration_programs` `government_sources` `government_documents` `document_versions` `document_chunks` `fetch_log`

### Phase 2 Tables

`requirements` `requirement_evidence` `requirement_versions` `requirement_references` `extraction_runs` `requirement_review`

### Phase 3C Models (in-memory, not database)

`ApplicantProfile` `VisaSelection` `Rule` `Provenance` `RuleEvaluation` `EligibilityOutcome` `Gap` `QuestionDefinition`

---

## 6. Test Inventory

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

### Phase 2 (51 tests)

| File | Tests | Coverage |
|------|-------|----------|
| test_schema.py | 11 | JSON Schema validation, logic rules |
| test_evidence.py | 8 | Exact substring verification, hallucination detection |
| test_normalization.py | 8 | Operator/unit/category normalization |
| test_deduplication.py | 8 | Semantic dedup, conflict detection |
| test_extraction.py | 9 | LLM contract tests, mock responses |
| test_integration.py | 4 | Full pipeline integration |
| test_gold_dataset.py | 3 | Gold standard evaluation |

### Total: 340 tests, all passing, 0 warnings

---

## 7. Key Files

### Phase 3

- `src/phase3/models.py` — Rule, Provenance, RuleEvaluation, EligibilityResult
- `src/phase3/operators.py` — 12 declarative operators
- `src/phase3/expressions.py` — 7 expression types, recursive evaluator
- `src/phase3/rule_compiler.py` — Phase 2 requirement → Phase 3 rule
- `src/phase3/rule_engine.py` — Single rule + aggregate evaluation
- `src/phase3/explanations.py` — Human-readable evaluation explanations

### Phase 3C

- `src/phase3c/applicant.py` — ApplicantProfile, VisaSelection
- `src/phase3c/applicability.py` — Program/status/version filtering
- `src/phase3c/assessment.py` — ELIGIBLE/INELIGIBLE/INCOMPLETE mapping
- `src/phase3c/gap_engine.py` — Missing field detection + prioritization
- `src/phase3c/questions.py` — Deterministic question templates + validation

### Baselines

- `validation/phase3_baseline_manifest.json` — Phase 3 frozen checkpoint
- `validation/run_report.json` — Phase 1 pipeline results

---

## 8. Design Principles

1. **Declarative rules only**: No lambdas, no dynamic code execution. Rules are data. Engine interprets generically.

2. **Evidence is verbatim**: Every extracted requirement must have evidence that is an exact substring of its source chunk.

3. **Missing = UNKNOWN, not FAIL**: `None` fields produce `UNKNOWN`, preserving the distinction between "we don't know" and "rule failed."

4. **No LLM in eligibility**: LLMs extract knowledge (Phase 2). Rules decide eligibility (Phase 3). RAG explains rules (future). None overlap.

5. **Historical integrity**: Rule v1 assessments stay v1. Government changes create v2. Historical users are never evaluated against current rules.

6. **Provenance preserved**: Every rule traces back through: requirement → chunk → document version → document → source → government URL.

7. **Four-state is necessary**: PASS/FAIL is insufficient. UNKNOWN ("we don't know your points") must not become FAIL ("you don't have enough points"). NOT_APPLICABLE ("189 rules don't apply to 190 applicants") must not pollute AND/OR.

8. **Gap priority from metadata**: CRITICAL/HIGH/MEDIUM/LOW comes from rule `critical`/`mandatory` flags, not arbitrary UI logic.
