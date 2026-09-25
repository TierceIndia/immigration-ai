# LINEAGE + IMMUTABILITY + INTEGRITY AUDIT REPORT
## Immigration Knowledge Platform — Schema & Provenance Hardening (Audit Items P0/P2)
**Report Date**: September 8, 2026
**Status**: ✅ 873 tests PASSING | Live PostgreSQL migrated to `f7a6d5c4b3a2` | 11/11 live-schema smoke probes PASS

> **STATUS UPDATE — 2026-09-15** (this report is a historical snapshot of the lineage/immutability
> hardening; its findings remain valid). Since publication the suite has grown to **899 collected /
> 897 passed / 2 skipped / 0 failures** (30 warnings), the source registry now seeds **14 sources
> across all four visa categories**, `POST /api/v1/admin/sync` was fixed to be admin-gated (was
> unauthenticated) and all admin routes re-audited, and the manual Playwright script was moved out
> of `tests/` so pytest no longer launches a browser at collection.
>
> **STATUS UPDATE — 2026-09-25**: the provenance/lineage chain this report hardened was
> **exercised for the first time end-to-end against real PostgreSQL** via
> `tests/e2e/test_full_knowledge_to_assessment.py` (now passing): a real assessment traces back
> through rule → requirement → evidence → chunk → document version → document → source, and
> `Gap.field` was confirmed to preserve the compiled rule's true field name (not a normalized
> alias) for full traceability. Getting there also surfaced and fixed a severe, previously-latent
> bug where the *evaluation* layer (not lineage/provenance itself) would have silently returned
> UNKNOWN for every real rule regardless of applicant data — see `PLAN.md` Section 2 for detail.
> Current, authoritative status: `README.md` → "What works / what doesn't".

---

## 1. Objective

Close the two top audit gaps on the deterministic assessment platform:

- **P0 — Regulatory provenance / lineage**: every assessment, rule result, requirement,
  evidence link and extraction run must trace back to the exact government snapshot
  (source → document → document version → chunk, and requirement version → rule version).
- **P2 — Immutability / integrity**: versioned history must be append-only, mutation of
  released artifacts must be blocked, and vocabulary/duplication drift must be rejected at
  the schema level.

---

## 2. What was delivered

### 2.1 Schema migration `f7a6d5c4b3a2_lineage_and_immutability.py`
(down_revision `0ceaaa268719`; applied, then baked into the API image)

**Canonical version-id lineage columns** (all nullable; writers populate them on real paths,
app-layer guard raises when unresolved — see §4 drift notes):

| Table | New column | Targets |
|---|---|---|
| `extraction_runs` | `document_version_id` | `document_versions.version_id` |
| `requirements` | `document_version_id` | `document_versions.version_id` |
| `requirement_evidence` | `document_version_id` | `document_versions.version_id` |
| `requirement_versions` | `document_version_id` | `document_versions.version_id` |
| `rules` | `requirement_version_id` | `requirement_versions.version_id` |
| `rule_versions` | `requirement_version_id` | `requirement_versions.version_id` |
| `assessment_rule_results` | `rule_version_id` | `rule_versions.version_id` |
| `assessments` | `ruleset_id` | `rulesets.ruleset_id` |

**Immutable ruleset ledger**: `rulesets` (program, content_hash, status DRAFT/RELEASED, rule_count)
+ `ruleset_rules` (immutable member `rule_versions`, ordered position). A ruleset pins the exact
set of rule versions an assessment was scored against.

**Constraint hardening** (raw DDL — Alembic's `create_foreign_key`/`create_check_constraint`
path crashes in this env with `TypeError: unhashable type: 'list'`, so FKs/CHECKs are emitted
as explicit `ALTER TABLE … ADD CONSTRAINT`):

- 16 composite **foreign keys**, including the three-column
  `fk_evidence_chunk_lineage (chunk_id, document_id, document_version_id) →
  document_chunks (chunk_id, document_id, version_id)` that pins an evidence link to its exact
  chunk *and* version, and `fk_req_program_country (program_id, country_id)`,
  `fk_rule_req_program_pair (requirement_id, program_id)`, `fk_src_program_country`,
  `fk_doc_program_country`.
- 9 **unique indexes** (raw `CREATE UNIQUE INDEX IF NOT EXISTS`): `(document_id, version_id)`,
  `(document_id, content_hash)`, `(program_id, country_id)` on `immigration_programs`,
  `(requirement_id, program_id)`, `(version_id, order_index)` and `(chunk_id, document_id, version_id)`
  on chunks, partial `(program_id, semantic_key) WHERE status NOT IN ('CONFLICT','REJECTED')`,
  `(assessment_id, rule_id)`, plus `idx_ruleset_rule_version`.
- 19 **CHECK constraints** unifying code + legacy vocabulary across all history tables
  (e.g. `change_type IN ('INITIAL','CHANGED','UNCHANGED','MANUAL_CAPTURE')`,
  requirement/rule/assessment/result status vocabularies, `document_version >= 0` on
  `requirement_versions`, positive version numbers).
- **Monetary precision**: `llm_usage.cost_usd`, `llm_budget_log.total_cost_usd`,
  `llm_budget_log.budget_limit_usd` → `NUMERIC(16,6)` (floats never summed for money).
- **PG append-only triggers**: `fn_block_mutation` on `document_versions`,
  `requirement_versions`, `rule_versions`, `assessments`, `assessment_rule_results`,
  `rulesets`, `ruleset_rules` — UPDATE/DELETE raises. `fn_reject_fallback_self_ref` on
  `government_sources` blocks a source falling back to itself.
- **Backfills** for the new lineage ids on existing rows (matching `document_version`,
  latest `requirement_version`, latest `rule_version`), idempotent.
- **Preflight guard** (§checks): migration refuses to apply if rows already violate the target
  constraints (20 data-consistency probes).
- **Downgrade** reverses everything in strict reverse order.

### 2.2 Application / ORM sync

- `src/db/models.py`: new columns, `rulesets`/`ruleset_rules` tables, and ORM-level
  equivalents of the unique/check constraints (incl. `uq_doc_version_identity` and
  `uq_req_program_pair` on `DocumentVersion`/`Requirement` — required so SQLite `create_all`
  enforces them in tests via `PRAGMA foreign_keys=ON`).
- `src/services/assessment_service.py`: `run_assessment` resolves current rule versions,
  snapshots an immutable `ruleset` (`_acquire_ruleset`), and passes `ruleset_id` +
  per-rule `rule_version_id` into `create()`; `get()` returns them. `_compute_ruleset_hash`
  accepts `(rule_id, version)` tuples or `Rule` dataclasses (backward compatible with tests).
- `src/db/repositories.py`: `SourceRepo.assert_safe_fallback` (rejects self-ref, missing
  target, and cycles by walking the chain) invoked on `SourceRepo.create`; `RulesetRepo`.
- `src/api/routes/admin_sources.py` manual capture: hash-idempotent — re-capturing identical
  content produces an `UNCHANGED` re-version-path once, without duplicating versions/chunks/
  extraction work.
- `src/api/schemas/models.py`: `ruleset_id`/`rule_version_id` surfaced on responses.
- `src/phase2/requirement_repository.py`: `create_extraction_run` now resolves
  `document_version_id`, reuses matching RUNNING/COMPLETED runs (never FAILED), and its dedup
  SELECT dropped a legacy-only `ORDER BY started_at` (drift resolver rewrites INSERTs only,
  not SELECTs).

---

## 3. Verification

| Check | Result |
|---|---|
| Full test suite | **873 passed** (847 baseline + 26 new `tests/test_lineage_integrity.py`) in ~65 s, `PYTHONPATH="src;src/phase2"` |
| New integrity tests cover | CHECK vocab, UNIQUE (incl. partial active-semantic dedup), FK enforcement under `PRAGMA foreign_keys=ON`, ruleset idempotency/immutability/trace, fallback guard, legacy-SQLite extraction-run idempotency |
| Live PostgreSQL | `alembic current` = `f7a6d5c4b3a2` (head); migrations now ship in the image (`docker compose build api` + recreate, container start runs `alembic upgrade head`) |
| Live-schema smoke probes | **11/11 PASS**: trigger blocks UPDATE/DELETE on `document_versions` and `rulesets` (P0001), fallback self-ref rejected, FK pair/lineage violations (23503), CHECK vocab (23514), UNIQUE dup (23505) |
| Pre-existing `rulesets`/`ruleset_rules` tables (ad hoc exploratory SQL, DDL matched the migration) | migration now guards creation via `information_schema` checks and reuses them; no data lost |

---

## 4. Known constraints / drift (documented, deliberate)

1. **All new lineage columns remain nullable.** Advanced writers populate them; legacy/direct
   SQL cannot be forced to. The app guard (`rule_version_map` fallback, ValueError on missing
   document version) and the resolver path make unresolved lineage visible rather than silent.
2. **PG unique indexes vs SQLite UNIQUE constraints.** PG enforces the lineage uniques as raw
   unique indexes; SQLite/ORM uses table-level UNIQUE constraints. Identical semantics, different
   DDL. ORM constraint *names* (`uq_doc_version_identity`) differ cosmetically from the PG index
   names (`uq_doc_ver_docid_versionid`) — worth reconciling in a future schema-tooling pass.
3. **Triggers are PostgreSQL-only.** SQLite test engines (ORM `create_all`, legacy `schema.sql`)
   cannot run plpgsql; append-only enforcement there is covered by tests, not DDL.
4. **Legacy SQLite approximation.** `schema.sql` + `phase2_requirements.sql` carry single-column
   lineage FKs only; full composite pair FKs exist on PG/ORM. The legacy engine is the documented
   "drift-resolver" path.
5. **`extraction_run_id` on `requirements` is NOT NULL** in the live schema (observed during
   smoke tests) while the ORM allows NULL — insert probes must supply an existing run id.
6. **`applicants` table** does not exist on live PG (referenced by `assessments.applicant_id`
   → `applicant_profiles.applicant_id`); cumbersome — not part of this work, flagged for the
   schema tooling pass.

---

## 5. How to re-verify

```bash
# schema state on live PG
docker exec immigration-knowledge-api-1 alembic current        # => f7a6d5c4b3a2 (head)
docker exec immigration-knowledge-api-1 alembic history
# integrity suite (local)
$env:PYTHONPATH="src;src/phase2"; python -m pytest tests -q    # 873 passed
# live DDL dump (see DBSTRUCTURE.md, regenerated from live PG)
docker exec immigration-knowledge-postgres-1 pg_dump -U immigration -d immigration --schema-only
# rollback path
docker exec immigration-knowledge-api-1 alembic downgrade 0ceaaa268719
```

`DBSTRUCTURE.md` was regenerated from the live database (30 tables, exact counts, columns,
FKs, unique indexes, CHECKs, triggers, migration status).

---

## 6. Remaining recommendations

- Seed `rulesets`/assessment flows with realistic data and add a round-trip E2E assessment test
  against live PG (currently 0 rows in requirements/rules/assessments).
- Reconcile ORM-vs-migration constraint naming in one automated schema diff guard (CI) so the
  three schema shapes (PG Alembic / ORM SQLite / legacy SQLite) can never silently diverge.
- Review `requirements.extraction_run_id` NULL-ability in the ORM to match live behavior.
- General production hardening (JWT secret, secrets rotation) — outside this audit item.