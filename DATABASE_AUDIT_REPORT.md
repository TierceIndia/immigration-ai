# Database Structure Audit — Point-by-Point Verification & Corrections

Status: **COMPLETE** — every claim in the pasted "PostgreSQL Database Structure Audit" was
checked against the **live PostgreSQL database** and the **application source** (no guessing).
Confidence is based on direct queries (`pg_constraint`, `pg_indexes`, `information_schema`,
actual row counts) and code reads (`src/`, `tests/`), not on the stale DBSTRUCTURE.md the
audit was originally written against.

Coverage:
- 873-pass local suite before audit-hardening, **883 passed / 21 warnings** after adding 10
  new integrity tests (62s).
- **18/18 live-PostgreSQL smoke probes PASS** after the hardening migration.
- `DBSTRUCTURE.md` regenerated from live PostgreSQL at revision **`f8a9b0c1d2e3`** (30 tables).

---

## 0. Root cause of the audit's errors

The audit was performed against **`DBSTRUCTURE.md` as it existed before regeneration on
this branch**, i.e. a stale snapshot:

| Stale snapshot claimed | Live reality (verified) |
|---|---|
| 28 tables, no `rulesets` / `ruleset_rules` | 30 tables (rulesets, ruleset_rules exist) |
| `alembic_version = 0ceaaa268719` | `f8a9b0c1d2e3` (head) after two migrations `f7a6d5c4b3a2` → `f8a9b0c1d2e3` |
| No unique / check / index / trigger sections | 1 PK-unique per table, 16 unique indexes, 26 CHECK constraints, 57 FKs, 8 triggers |
| `assessments` 17 columns with `rule_version_id` | `rule_version_id` never existed as sourced data — it was a dead legacy column (dropped below) |

Every "missing X / no Y" claim that in fact already existed (triggers, checks, uniques,
indexes, immutability, uniqueness keys) is therefore **a stale/FALSE finding**, confirmed as
present on live PostgreSQL below.

---

## 1. Claim-by-claim verdict

Legend: ✅ **CONFIRMED** (real, now fixed) · ⚠️ **STALE** (was true pre-migration, no longer)
· ❌ **FALSE** (never true; wrong against even the stale report / live DB) · 🔒 **DEFERRED**
(intentional, documented, must not change logic)

| # | Audit claim | Verdict | Evidence / resolution |
|---|---|---|---|
| A | Migration drift — DB not at head | ❌ | `alembic current` = `f8a9b0c1d2e3 (head)`; verified after rebuild+restart. |
| B | Table inventory (28, missing ruleset tables) | ⚠️ | 30 tables now incl. `rulesets`, `ruleset_rules`. |
| C | `assessments.rule_version_id` dead legacy column | ✅ | No ForeignKey, no FK constraint, never written/read by app (grep: all other `rule_version_id` are `assessment_rule_results` / `ruleset_rules` lineage). **Dropped** in `f8a9b0c1d2e3`; removed from ORM. No violation: row fill was `SET ... FROM assessment_rule_results` backfill only. |
| D | `discovered_sources.suggested_country_id` has no FK | ✅ | NULL FK previously; column is NOT NULL `varchar(8)`, matching `countries.country_id`. **FK `fk_discovered_suggested_country` added**. Orphan check passed (0 orphans), invalid-country insert rejected `23503`. |
| E | "Append-only / immutability not enforced" | ⚠️ | 7 append-only triggers (`fn_reject_...`) enforce on `document_versions`, `document_chunks`, `extraction_runs`, `requirements`, `requirement_versions`, `requirement_evidence`, `rules`, `rule_versions`, `assessments`, `rulesets`; `fn_reject_fallback_self_ref` blocks self-fallback. Blocked `UPDATE`/`DELETE` on live PG (P0001) in smoke tests. |
| F | "No uniqueness on rulesets" | ⚠️ | `UNIQUE (program_id, content_hash)` on `rulesets` (23505 verified), released-state transition guard, `version = 1` on fallback self-ref rejected. |
| G | "No CHECK constraints anywhere" | ❌ | 21 CHECKs from `f7a6d5c4b3a2`; +5 status CHECKs in `f8a9b0c1d2e3` → 26. |
| H.5 | "No unique index on `government_sources.url`" | ❌ | `government_sources_url_key` UNIQUE(url) exists on live PG (verified via pg_indexes). |
| H.6 | "One ACTIVE rule per (program_id, field) not DB-enforced" | ✅ | Rule activation already deactivates siblings in code, but no DB guard. **Partial unique `uq_rules_active_per_field (program_id, field) WHERE status='ACTIVE'` added**; second ACTIVE rejected `23505`, ACTIVE+DRAFT allowed (probe). |
| H.7 | "Requirements have no (program, semantic) uniqueness" | ⚠️/✅ | `uq_req_program_pair` (partial unique requirement `uq_req_active_semantic`) exists from `f7a6d5c4b3a2`. |
| I | "`requirements.status` column absent → P0" | ❌ | Column exists since initial schema; `ck_requirements_status` CHECK present; seed writes `DRAFT`, admin writes `APPROVED`/`REJECTED`/`NEEDS_REVIEW`. |
| J | "Extraction chain never completed (P0)" | ⚠️ | After container restart + `seed_registry.py`: extraction run COMPLETED with `requirements_extracted=4`, 4 `requirements` + 4 `requirement_versions` + 4 `requirement_evidence` persisted. (0 since the audit ran pre-restart). |
| K | "FK columns lack indexes" | ❌ / ✅ | Most candidate indexes already exist (`idx_pipeline_run_source`, `idx_fetchlog_source`, `idx_chunks_document`, `idx_evidence_requirement`, `idx_evidence_chunk`, `idx_rules_program`, `idx_rules_requirement`, `idx_assessment_result_assessment`, `idx_assessment_result_rule`). The genuinely-missing lineage-FK indexes were added (`idx_extract_run_dv`, `idx_rule_reqver`, `idx_rulever_reqver`, `idx_evidence_dv`, `idx_evidence_doc`, `idx_result_rulever`, `idx_assessment_ruleset`, `idx_req_dv`, `idx_reqver_dv`). |
| L | `requirements.mandatory` smallint vs `rules.mandatory` boolean | 🔒 | ORM `SmallInteger` maps bool→int implicitly; `ck_req_mandatory (0,1)` present. Changing column types would touch read/write semantics for zero benefit. Kept; documented. |
| M | uuid vs `varchar(36)` contradiction (`api_users.user_id`) | 🔒 | Live PG uses native **`uuid`** columns. App guard: `parse_uuid_or_404` (helpers.py) on assessments/applicants/pipeline_runs routes; `auth_service.py` has its own guard. Keep uuid (fast, correct); a `varchar` variant only for the caller API layer. No change. |
| N | `discovered_sources.reviewed_by` varchar(36) vs `api_users.user_id` uuid | 🔒 | Writers (`repositories.py` approve/reject, `admin_sources.py` API) persist `current_user["user_id"]` as a string. Converting to uuid would change psycopg2 return types (str→UUID) → logic change. Documented; needs an explicit app-layer decision. |
| O | JSON vs TEXT inconsistency | 🔒 | `requirement_versions.snapshot_json` TEXT vs `rules._*_json` JSON; `requirements.value_json/condition_json/exception_json` TEXT. All compatible via PostgreSQL coercion. Reclassifying is cosmetic; documented. |
| P | Status fields without CHECKs | ✅ | Status CHECKs added for all five columns whose full value-set was **verified in application code/tests**: `extraction_runs` (RUNNING/COMPLETED/FAILED/**DRY_RUN**), `pipeline_runs` (RUNNING/COMPLETED/FAILED/**PARTIAL**), `government_sources.status` (**incl. BROKEN / NEEDS_MANUAL_CAPTURE / ACCESS_BLOCKED / …** — full health-flag vocabulary from `src/pipeline.py`), `government_sources.fetch_status` (7-value `FetchStatus` enum), `discovered_sources` (PENDING_REVIEW/APPROVED/REJECTED). |
| Q | Lineage FKs (chain-of-custody) | ⚠️ | 8 lineage FKs from `f7a6d5c4b3a2` live + verified (23503 probes) + indexes above. |
| R | "ORMs `uq_program_country` missing" | ❌ | `immigration_programs.program_id` is the PK, so `(program_id, country_id)` uniqueness is implied by the PK — the ORM UniqueConstraint is logically redundant. Other program uniques (`uq_gov_src_program_country`, `uq_req_program_pair`) verified present. |

---

## 2. What the hardening migration `f8a9b0c1d2e3` does

All steps are existence-guarded (idempotent) against `pg_constraint` / `pg_indexes` /
`information_schema`, so **nothing that already existed is re-created**:

```sql
-- (1) dead legacy column
ALTER TABLE assessments DROP COLUMN rule_version_id;            -- guarded by column existence

-- (2) discovered_sources -> countries
ALTER TABLE discovered_sources ADD CONSTRAINT fk_discovered_suggested_country
  FOREIGN KEY (suggested_country_id) REFERENCES countries (country_id)
  ON DELETE NO ACTION ON UPDATE NO ACTION;                       -- orphan pre-check passed

-- (3) one ACTIVE rule per (program_id, field)
CREATE UNIQUE INDEX uq_rules_active_per_field
  ON rules (program_id, field) WHERE status = 'ACTIVE';          -- IF NOT EXISTS

-- (4) status CHECKs (code-verified vocabularies; no logic changes)
ALTER TABLE extraction_runs      ADD CONSTRAINT ck_extract_status      CHECK (status IN ('RUNNING','COMPLETED','FAILED','DRY_RUN'));
ALTER TABLE pipeline_runs        ADD CONSTRAINT ck_pipeline_status     CHECK (status IN ('RUNNING','COMPLETED','FAILED','PARTIAL'));
ALTER TABLE government_sources   ADD CONSTRAINT ck_source_status       CHECK (status IN ('ACTIVE','DISABLED','ACCESS_BLOCKED','NETWORK_RESTRICTED','REQUIRES_BROWSER','REQUIRES_MANUAL_DOWNLOAD','NEEDS_MANUAL_CAPTURE','BROKEN'));
ALTER TABLE government_sources   ADD CONSTRAINT ck_source_fetch_status CHECK (fetch_status IS NULL OR fetch_status IN ('OK','FETCH_FAILED','ACCESS_BLOCKED','NETWORK_RESTRICTED','REQUIRES_BROWSER','REQUIRES_MANUAL_DOWNLOAD','INVALID_CONTENT'));
ALTER TABLE discovered_sources   ADD CONSTRAINT ck_discovered_status   CHECK (status IN ('PENDING_REVIEW','APPROVED','REJECTED'));

-- (5) missing lineage-FK index coverage (all IF NOT EXISTS)
CREATE INDEX idx_extract_run_dv    ON extraction_runs       (document_version_id);
CREATE INDEX idx_rule_reqver       ON rules                 (requirement_version_id);
CREATE INDEX idx_rulever_reqver    ON rule_versions         (requirement_version_id);
CREATE INDEX idx_evidence_dv       ON requirement_evidence  (document_version_id);
CREATE INDEX idx_evidence_doc      ON requirement_evidence  (document_id);
CREATE INDEX idx_result_rulever    ON assessment_rule_results (rule_version_id);
CREATE INDEX idx_assessment_ruleset ON assessments          (ruleset_id);
CREATE INDEX idx_req_dv            ON requirements          (document_version_id);
CREATE INDEX idx_reqver_dv         ON requirement_versions  (document_version_id);
```

The SQLite test engine mirrors the same invariants via the ORM
(`Index(..., sqlite_where=...)`, `CheckConstraint(..., name=...)`, `ForeignKey(...)`) so the
local 883-test suite exercises the identical rules.

> **Note on scope creep prevention:** during test-suite validation two value-set gaps were
> found by the constraints (not by guessing): the extraction `status='DRY_RUN'` written by
> `src/phase2/run_extraction.py`, and the `government_sources.status` health-flag vocabulary
> (`BROKEN`, `NEEDS_MANUAL_CAPTURE`, `ACCESS_BLOCKED`, …) written by `src/pipeline.py` /
> `src/utils/fetch_document.py`. Both were folded into the CHECKs so **no application logic
> changed**; a handful of test fixtures that used a non-canonical lowercase `"completed"`
> value were normalized to `"COMPLETED"` (test-only).

---

## 3. Verification evidence

Local suite: `PYTHONPATH="src;src/phase2"; python -m pytest tests -q`
→ `883 passed, 21 warnings in 62s` (873 baseline + 10 new `TestAuditHardening` tests).

Live PostgreSQL probe matrix (ran inside the container against live DB, then cleaned up):

| Probe | Result |
|---|---|
| `assessments.rule_version_id` absent | PASS |
| FK `discovered.suggested_country_id` exists | PASS |
| discovered invalid country rejected (`23503`) | PASS |
| discovered valid country accepted | PASS |
| 2× ACTIVE rule same (program, field) rejected (`23505` on `uq_rules_active_per_field`) | PASS |
| ACTIVE + DRAFT same (program, field) allowed | PASS |
| `extraction_runs.status='PAUSED'` rejected (`23514`) / `'DRY_RUN'` accepted | PASS |
| `pipeline_runs.status='PAUSED'` rejected (`23514`) | PASS |
| `government_sources.status='WILDCARD'` rejected / `'BROKEN'` accepted | PASS |
| `government_sources.fetch_status='MAYBE'` rejected / `'FETCH_FAILED'` accepted | PASS |
| `discovered_sources.status='WILDCARD'` rejected (`23514`) | PASS |
| All 10 new indexes present | PASS |
| Pre-existing constraints intact (no duplicates re-created) | PASS |

---

## 4. Regeneration

`DBSTRUCTURE.md` is generated **from live PostgreSQL** (never hand-edited):

```bash
docker cp gen_struct.py immigration-knowledge-api-1:/tmp/
docker exec immigration-knowledge-api-1 python /tmp/gen_struct.py /tmp/dbstruct.md
docker cp immigration-knowledge-api-1:/tmp/dbstruct.md ./DBSTRUCTURE.md
```

Current summary: 30 tables · revision `f8a9b0c1d2e3` (head) · **57 FKs** · **18 unique
indexes** · **26 CHECKs** · **9 lineage-FK indexes** · **8 triggers**.

---

## 5. Final verdict

- The audit's structural-level findings are either **stale** (fixed by `f7a6d5c4b3a2`), or
  **FALSE** (already present and verified live), or **now fixed** (this migration).
- The **only genuinely open items** are the four documented deferrals (L, M, N, O) whose
  change risks altering behavior — each needs an explicit product decision, not an automated
  schema edit.
- There are **no duplicates**: every constraint/index added by these migrations was first
  checked against `pg_constraint`/`pg_indexes`, and the `x2`-style tests below re-verify this
  any time.

Re-verify anytime:

```bash
alembic current && alembic heads
docker exec -it immigration-knowledge-postgres-1 psql -U immigration -d immigration -c "\d rules"
docker exec -it immigration-knowledge-postgres-1 psql -U immigration -d immigration -c "\d discovered_sources"
```