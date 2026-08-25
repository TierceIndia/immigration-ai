-- Phase-2 schema: Requirement Knowledge Model.
-- Purely additive to schema.sql - no Phase-1 table is altered, dropped, or renamed here.
-- Chain: government_documents/document_versions/document_chunks (Phase-1)
--          -> requirements -> requirement_evidence (Phase-2)
--          -> requirement_versions / requirement_review / requirement_references
--          -> extraction_runs (audit trail of every extraction attempt)

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- 1. extraction_runs  (one row per attempt to extract requirements from a document version)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS extraction_runs (
    run_id                TEXT PRIMARY KEY,          -- 'RUN-<uuid4-short>'
    document_id           TEXT NOT NULL REFERENCES government_documents(document_id),
    document_version      INTEGER NOT NULL,
    started_at            TEXT NOT NULL,
    completed_at          TEXT,
    model                 TEXT NOT NULL,              -- e.g. 'mock-llm-v1' or 'claude-sonnet-4-6'
    prompt_version         TEXT NOT NULL,              -- e.g. 'requirement_extraction_v1'
    chunks_seen            INTEGER NOT NULL DEFAULT 0,
    candidate_chunks       INTEGER NOT NULL DEFAULT 0,
    llm_calls              INTEGER NOT NULL DEFAULT 0,
    requirements_extracted INTEGER NOT NULL DEFAULT 0,
    validated               INTEGER NOT NULL DEFAULT 0,
    needs_review            INTEGER NOT NULL DEFAULT 0,
    rejected                INTEGER NOT NULL DEFAULT 0,
    evidence_failures        INTEGER NOT NULL DEFAULT 0,
    schema_failures          INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'RUNNING' CHECK (status IN
                     ('RUNNING','COMPLETED','FAILED','DRY_RUN')),
    dry_run                 INTEGER NOT NULL DEFAULT 0 CHECK (dry_run IN (0,1)),
    error                    TEXT
);

CREATE INDEX IF NOT EXISTS idx_extraction_runs_document ON extraction_runs(document_id, document_version);

-- ---------------------------------------------------------------------------
-- 2. requirements  (current structured state of each distinct requirement)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS requirements (
    requirement_id     TEXT PRIMARY KEY,            -- 'REQ-<program>-<short-hash>'
    semantic_key        TEXT NOT NULL,               -- stable identity for dedup/versioning across doc versions
    program_id           TEXT NOT NULL REFERENCES immigration_programs(program_id),
    country_id           TEXT NOT NULL REFERENCES countries(country_id),
    category              TEXT NOT NULL,              -- controlled taxonomy, see requirements.schema.json
    name                  TEXT NOT NULL,
    description            TEXT,
    field                 TEXT,                       -- e.g. 'applicant.age'  (nullable: informational content has no field)
    operator               TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK (operator IN
                     ('EQ','NEQ','LT','LTE','GT','GTE','IN','NOT_IN','BETWEEN',
                      'REQUIRED','NOT_REQUIRED','UNKNOWN')),
    value_json             TEXT,                       -- JSON-encoded value (number/string/array/bool/null)
    unit                   TEXT,                       -- normalized unit, see requirements.schema.json
    mandatory               INTEGER CHECK (mandatory IN (0,1)),  -- NULL = unknown
    condition_json          TEXT,                       -- JSON: {"field":..,"operator":..,"value":..} or null
    exception_json           TEXT,                       -- JSON: {"description":..,"condition":..} or null
    applies_to               TEXT,                       -- free text scope note, e.g. 'regional stream only'
    content_kind              TEXT NOT NULL DEFAULT 'REQUIREMENT' CHECK (content_kind IN
                     ('REQUIREMENT','FACT','INFORMATIONAL','PROCEDURE','EXCEPTION','CROSS_REFERENCE')),
    confidence                 REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    criticality                TEXT NOT NULL DEFAULT 'NORMAL' CHECK (criticality IN
                     ('CRITICAL','IMPORTANT','NORMAL','INFORMATIONAL')),
    status                     TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN
                     ('DRAFT','VALIDATED','NEEDS_REVIEW','APPROVED','REJECTED','SUPERSEDED','CONFLICT')),
    current_version             INTEGER NOT NULL DEFAULT 1,
    extraction_run_id            TEXT NOT NULL REFERENCES extraction_runs(run_id),
    created_at                    TEXT NOT NULL,
    updated_at                    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_requirements_program ON requirements(program_id);
CREATE INDEX IF NOT EXISTS idx_requirements_semantic_key ON requirements(semantic_key);
CREATE INDEX IF NOT EXISTS idx_requirements_status ON requirements(status);
CREATE INDEX IF NOT EXISTS idx_requirements_category ON requirements(category);

-- ---------------------------------------------------------------------------
-- 3. requirement_evidence  (one-to-many: a requirement can be supported by multiple chunks)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS requirement_evidence (
    evidence_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_id       TEXT NOT NULL REFERENCES requirements(requirement_id),
    document_id           TEXT NOT NULL REFERENCES government_documents(document_id),
    document_version       INTEGER NOT NULL,
    chunk_id                INTEGER NOT NULL REFERENCES document_chunks(chunk_id),
    evidence_text            TEXT NOT NULL,             -- must be a verified substring of the chunk's raw_text
    evidence_type             TEXT NOT NULL CHECK (evidence_type IN
                     ('DIRECT_TEXT','TABLE','LIST','CROSS_REFERENCE','MULTI_CHUNK')),
    verified                  INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
    verification_note          TEXT,
    extraction_method           TEXT NOT NULL DEFAULT 'LLM_EXTRACTION',
    extracted_at                TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidence_requirement ON requirement_evidence(requirement_id);
CREATE INDEX IF NOT EXISTS idx_evidence_chunk ON requirement_evidence(chunk_id);

-- ---------------------------------------------------------------------------
-- 4. requirement_versions  (append-only structured snapshot history)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS requirement_versions (
    version_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_id     TEXT NOT NULL REFERENCES requirements(requirement_id),
    version_number       INTEGER NOT NULL,
    snapshot_json          TEXT NOT NULL,               -- full structured requirement at this version
    document_version        INTEGER NOT NULL,           -- which source document_version produced/confirmed this
    change_type              TEXT NOT NULL CHECK (change_type IN
                     ('INITIAL','UNCHANGED','ADDED','REMOVED','MODIFIED','UNCERTAIN','CONFLICTING','HUMAN_EDIT')),
    created_at                TEXT NOT NULL,
    created_by                 TEXT NOT NULL DEFAULT 'system',   -- 'system' (LLM/pipeline) or reviewer id
    UNIQUE(requirement_id, version_number)
);

CREATE INDEX IF NOT EXISTS idx_req_versions_requirement ON requirement_versions(requirement_id);

-- ---------------------------------------------------------------------------
-- 5. requirement_review  (human review audit trail)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS requirement_review (
    review_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_id    TEXT NOT NULL REFERENCES requirements(requirement_id),
    reviewer            TEXT NOT NULL,               -- CLI username / configured reviewer id
    action                TEXT NOT NULL CHECK (action IN
                     ('APPROVE','REJECT','EDIT','MARK_NEEDS_REVIEW','COMMENT')),
    old_state_json          TEXT,                     -- structured requirement before this action (nullable for COMMENT)
    new_state_json           TEXT,                     -- structured requirement after this action (nullable for pure comments)
    notes                     TEXT,
    created_at                 TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_requirement ON requirement_review(requirement_id);

-- ---------------------------------------------------------------------------
-- 6. requirement_references  (cross-references to other legal/guidance provisions)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS requirement_references (
    reference_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_id       TEXT NOT NULL REFERENCES requirements(requirement_id),
    reference_type          TEXT NOT NULL DEFAULT 'LEGAL_CROSS_REFERENCE' CHECK (reference_type IN
                     ('LEGAL_CROSS_REFERENCE','GUIDANCE_CROSS_REFERENCE','EXTERNAL_DOCUMENT')),
    target_text               TEXT NOT NULL,           -- verbatim reference text found in the source, e.g. 'Schedule 2, clause 189.2'
    resolved_chunk_id           INTEGER REFERENCES document_chunks(chunk_id),  -- set only if resolved locally
    status                       TEXT NOT NULL DEFAULT 'UNRESOLVED' CHECK (status IN
                     ('UNRESOLVED','RESOLVED_LOCAL','NEEDS_REVIEW'))
);

CREATE INDEX IF NOT EXISTS idx_references_requirement ON requirement_references(requirement_id);
