# AU Immigration Knowledge Platform

End-to-end deterministic immigration eligibility system for Australian skilled migration visas.

**Phases completed**: 1 → 2 → 3 → 3C (all fully tested, 340 tests passing)

**Core principle**: Every eligibility decision is deterministic, auditable, and traceable to exact government evidence. No LLM decides eligibility.

## Quick Start

```bash
pip install -r requirements.txt
python -m pytest tests/ -q    # 340/340 passing
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
                   APPROVED RULES
                         │
                         ▼
                ┌─────────────────┐
                │ PHASE 3         │
                │ Deterministic   │
                │ Rule Engine     │
                │ 12 Operators    │
                │ 7 Expr Types    │
                │ IF/THEN, OR, AND│
                 │ Exceptions     │
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

## Test Suite

```bash
python -m pytest tests/ -q              # Full suite: 340 tests
python -m pytest tests/phase3 -q        # Phase 3 core: 192 tests
python -m pytest tests/phase3c -q       # Phase 3C eligibility: 97 tests
```

| Layer | Tests | What it proves |
|-------|-------|----------------|
| Phase 2 | 51 | Schema validation, evidence checking, normalization, deduplication |
| Phase 3 | 192 | Operators, expression tree, IF/THEN, exceptions, dependencies, versioning |
| Phase 3C | 97 | Applicant model, applicability, assessment, gap engine, questions |
| Other | 0 | — |
| **Total** | **340** | **All passing, 0 warnings** |

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

## Project Structure

```
immigration-knowledge/
├── src/
│   ├── phase3/                    # Rule engine (operators, expressions, compiler, engine)
│   ├── phase3c/                   # Eligibility + Gap Engine
│   │   ├── applicant.py           # ApplicantProfile, VisaSelection
│   │   ├── applicability.py       # Program/status/version filtering
│   │   ├── assessment.py          # ELIGIBLE/INELIGIBLE/INCOMPLETE
│   │   ├── gap_engine.py          # Missing field detection + prioritization
│   │   └── questions.py           # Deterministic question templates
│   ├── phase2/                    # Knowledge extraction (LLM, validation, dedup)
│   ├── extractor_html.py          # HTML → structured text
│   └── pipeline.py                # Phase 1 orchestrator
├── tests/
│   ├── phase3/                    # 192 tests (expression tree, operators, versioning)
│   ├── phase3c/                   # 97 tests (applicant, assessment, gaps)
│   └── phase2/                    # 51 tests (schema, evidence, dedup)
├── validation/
│   ├── phase3_baseline_manifest.json
│   └── run_report.json
├── schema/
│   ├── schema.sql                 # Phase 1-2 database schema
│   └── requirements.schema.json   # Phase 2 requirement schema
├── data/
│   ├── gold/                      # Gold standard datasets
│   └── real_validation/           # Frozen real Home Affairs chunks
└── prompts/
    └── requirement_extraction_v2.md
```

## Key Design Decisions

1. **Missing fields = UNKNOWN, not FAIL**: `ApplicantProfile` fields are `None` when missing. `to_rule_data()` excludes `None` fields. Rule engine returns `UNKNOWN` for missing fields.

2. **Deterministic at temperature=0**: All evaluations with gpt-4o at temperature=0 produce identical results on repeated runs.

3. **Evidence is verbatim**: Every extracted requirement must have evidence that is an exact substring of its source chunk. Fabricated evidence is hard-rejected.

4. **No LLM in eligibility**: LLMs are used only in Phase 2 (knowledge extraction). Phase 3 and 3C are purely deterministic. RAG explains rules but never decides eligibility.

5. **Versioned rules**: Rule v1 assessed against historical applicants stays v1. Government policy changes create v2; v1 is marked SUPERSEDED. Historical assessments are never corrupted.

6. **Gap priority from metadata**: Gap CRITICAL/HIGH/MEDIUM/LOW priority comes from rule `critical`/`mandatory` flags, not arbitrary UI logic.

## Running Against Real Data

```bash
# Requires OPENAI_API_KEY in .env
python src/phase2/run_extraction.py --document-id DOC-AU-189-MAIN --model gpt-4o

# Evaluate against real gold standard
python tests/phase2/test_real_gold_evaluation.py gpt-4o 0
```

## Baseline Manifests

- `validation/phase3_baseline_manifest.json` — Phase 3 frozen checkpoint
- `validation/run_report.json` — Phase 1 pipeline run results

## License

Internal project. Not for distribution.
