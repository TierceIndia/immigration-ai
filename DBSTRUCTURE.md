# Database Structure Report

**Regenerated**: 2026-09-25, directly from `db.schema_info.tables_snapshot()` — the same
function `GET /api/v1/admin/schema` calls — not hand-maintained. Row counts reflect a freshly
recreated local Postgres container at generation time (most operational tables show 0 rows);
re-run against your own environment for current counts. See `README.md` for narrative docs and
`PLAN.md` for current project status.

Backend: live PostgreSQL (exact counts via `SELECT COUNT(*)`).

> **Note on `primary_keys`/`foreign_keys`**: `tables_snapshot()`'s top-level `primary_keys` list
> currently includes PostgreSQL system catalog tables (`pg_*`) alongside the app's own 30 tables —
> it appears to introspect the whole database, not just the `public` schema. This report filters
> that list down to the app's own tables before use. Worth fixing in `db/schema_info.py` at some
> point (harmless today since it's filtered out here, but it also means the real
> `GET /api/v1/admin/schema` endpoint response includes the same noise).

## Migration status

- Current alembic revision applied to DB: `e4f6a1b2c3d4` (head)
- Migration files (8 present): 0ceaaa268719_schema_verification.py, 49b9088f3a03_initial_schema_from_models.py, a1b2c3d4e5f6_add_last_successful_fetch_at.py, c7d8e9f0a1b2_add_discovered_sources.py, d1f2e3a4b5c6_add_fallback_fields_to_government_sources.py, e4f6a1b2c3d4_add_fetch_error_type.py, f7a6d5c4b3a2_lineage_and_immutability.py, f8a9b0c1d2e3_audit_hardening.py

## Tables (30) — columns / current row counts

| Table | Cols | Rows |
|---|---|---|
| alembic_version | 1 | 0 |
| api_users | 9 | 1 |
| applicant_profiles | 19 | 0 |
| assessment_rule_results | 14 | 0 |
| assessments | 17 | 0 |
| audit_log | 10 | 0 |
| countries | 3 | 1 |
| discovered_sources | 16 | 0 |
| document_chunks | 10 | 0 |
| document_versions | 11 | 0 |
| extraction_runs | 21 | 0 |
| fetch_log | 10 | 0 |
| gaps | 11 | 0 |
| government_documents | 19 | 0 |
| government_sources | 24 | 14 |
| immigration_programs | 6 | 7 |
| llm_budget_log | 9 | 0 |
| llm_usage | 13 | 0 |
| pipeline_runs | 19 | 0 |
| requirement_evidence | 14 | 0 |
| requirement_references | 8 | 0 |
| requirement_review | 9 | 0 |
| requirement_versions | 10 | 0 |
| requirements | 24 | 0 |
| rule_versions | 9 | 0 |
| rules | 24 | 0 |
| ruleset_rules | 3 | 0 |
| rulesets | 9 | 0 |
| user_sessions | 7 | 0 |
| visa_selections | 6 | 0 |

## Column detail

### alembic_version

| column | type | nullable | PK |
|---|---|---|---|
| version_num | character varying(32) |  | PK |

### api_users

| column | type | nullable | PK |
|---|---|---|---|
| user_id | uuid |  | PK |
| email | character varying(256) |  |  |
| password_hash | character varying(256) |  |  |
| full_name | character varying(256) | yes |  |
| role | character varying(32) |  |  |
| is_active | boolean |  |  |
| last_login_at | timestamp with time zone | yes |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

### applicant_profiles

| column | type | nullable | PK |
|---|---|---|---|
| applicant_id | uuid |  | PK |
| user_id | uuid |  |  |
| first_name | character varying(128) | yes |  |
| last_name | character varying(128) | yes |  |
| date_of_birth | timestamp with time zone | yes |  |
| passport_nationality | character varying(8) | yes |  |
| current_visa_subclass | character varying(8) | yes |  |
| profile_json | json |  |  |
| age | integer | yes |  |
| english_level | character varying(32) | yes |  |
| points | integer | yes |  |
| occupation | character varying(32) | yes |  |
| skills_assessment_status | character varying(32) | yes |  |
| health_status | character varying(32) | yes |  |
| character_status | character varying(32) | yes |  |
| has_regional_nomination | boolean | yes |  |
| visa_subclass | character varying(8) | yes |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

Foreign keys:
- `applicant_profiles.user_id -> api_users.user_id`

### assessment_rule_results

| column | type | nullable | PK |
|---|---|---|---|
| result_id | uuid |  | PK |
| assessment_id | uuid |  |  |
| rule_id | character varying(48) |  |  |
| rule_version | integer |  |  |
| status | character varying(16) |  |  |
| field | character varying(128) | yes |  |
| actual_value | text | yes |  |
| expected_value | text | yes |  |
| operator | character varying(16) | yes |  |
| message | text | yes |  |
| reason | text | yes |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| rule_version_id | integer | yes |  |

Foreign keys:
- `assessment_rule_results.assessment_id -> assessments.assessment_id`
- `assessment_rule_results.rule_id -> rules.rule_id`
- `assessment_rule_results.rule_version_id -> rule_versions.version_id`

### assessments

| column | type | nullable | PK |
|---|---|---|---|
| assessment_id | uuid |  | PK |
| applicant_id | uuid |  |  |
| program_id | character varying(32) |  |  |
| business_status | character varying(16) |  |  |
| overall_status | character varying(16) |  |  |
| total_rules | integer |  |  |
| passed_count | integer |  |  |
| failed_count | integer |  |  |
| unknown_count | integer |  |  |
| not_applicable_count | integer |  |  |
| evaluated_at | timestamp with time zone |  |  |
| request_id | character varying(36) | yes |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| applicant_snapshot | json | yes |  |
| ruleset_hash | character varying(64) | yes |  |
| ruleset_id | character varying(36) | yes |  |

Foreign keys:
- `assessments.applicant_id -> applicant_profiles.applicant_id`
- `assessments.program_id -> immigration_programs.program_id`
- `assessments.ruleset_id -> rulesets.ruleset_id`

### audit_log

| column | type | nullable | PK |
|---|---|---|---|
| audit_id | uuid |  | PK |
| user_id | uuid | yes |  |
| action | character varying(64) |  |  |
| entity_type | character varying(64) |  |  |
| entity_id | character varying(64) | yes |  |
| before_json | json | yes |  |
| after_json | json | yes |  |
| request_id | character varying(36) | yes |  |
| ip_address | character varying(64) | yes |  |
| created_at | timestamp with time zone |  |  |

Foreign keys:
- `audit_log.user_id -> api_users.user_id`

### countries

| column | type | nullable | PK |
|---|---|---|---|
| country_id | character varying(8) |  | PK |
| name | character varying(128) |  |  |
| iso_code | character varying(8) |  |  |

### discovered_sources

| column | type | nullable | PK |
|---|---|---|---|
| discovered_source_id | character varying(32) |  | PK |
| seed_url | text |  |  |
| candidate_url | text |  |  |
| page_title | character varying(512) | yes |  |
| matched_keywords | json |  |  |
| confidence_score | integer |  |  |
| suggested_visa_category | character varying(32) | yes |  |
| suggested_country_id | character varying(8) |  |  |
| status | character varying(24) |  |  |
| discovered_at | timestamp with time zone |  |  |
| discovery_run_id | character varying(36) | yes |  |
| reviewed_by | character varying(36) | yes |  |
| reviewed_at | timestamp with time zone | yes |  |
| rejection_reason | text | yes |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

Foreign keys:
- `discovered_sources.suggested_country_id -> countries.country_id`

### document_chunks

| column | type | nullable | PK |
|---|---|---|---|
| chunk_id | integer |  | PK |
| document_id | character varying(32) |  |  |
| version_id | integer |  |  |
| chunk_type | character varying(16) |  |  |
| heading_path | character varying(512) | yes |  |
| order_index | integer |  |  |
| raw_text | text |  |  |
| page_number | integer | yes |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

Foreign keys:
- `document_chunks.document_id -> document_versions.document_id`
- `document_chunks.document_id -> document_versions.version_id`
- `document_chunks.document_id -> government_documents.document_id`
- `document_chunks.version_id -> document_versions.document_id`
- `document_chunks.version_id -> document_versions.version_id`
- `document_chunks.version_id -> document_versions.version_id`

### document_versions

| column | type | nullable | PK |
|---|---|---|---|
| version_id | integer |  | PK |
| document_id | character varying(32) |  |  |
| version_number | integer |  |  |
| content_hash | character varying(64) |  |  |
| retrieved_at | timestamp with time zone |  |  |
| raw_path | text |  |  |
| extracted_path | text | yes |  |
| change_type | character varying(16) |  |  |
| diff_summary | text | yes |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

Foreign keys:
- `document_versions.document_id -> government_documents.document_id`

### extraction_runs

| column | type | nullable | PK |
|---|---|---|---|
| run_id | character varying(24) |  | PK |
| document_id | character varying(32) |  |  |
| document_version | integer |  |  |
| model | character varying(64) |  |  |
| prompt_version | character varying(64) |  |  |
| chunks_seen | integer |  |  |
| candidate_chunks | integer |  |  |
| llm_calls | integer |  |  |
| requirements_extracted | integer |  |  |
| validated | integer |  |  |
| needs_review | integer |  |  |
| rejected | integer |  |  |
| evidence_failures | integer |  |  |
| schema_failures | integer |  |  |
| status | character varying(16) |  |  |
| dry_run | smallint |  |  |
| error | text | yes |  |
| completed_at | timestamp with time zone | yes |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| document_version_id | integer | yes |  |

Foreign keys:
- `extraction_runs.document_id -> document_versions.document_id`
- `extraction_runs.document_id -> document_versions.version_number`
- `extraction_runs.document_id -> government_documents.document_id`
- `extraction_runs.document_version -> document_versions.document_id`
- `extraction_runs.document_version -> document_versions.version_number`
- `extraction_runs.document_version_id -> document_versions.version_id`

### fetch_log

| column | type | nullable | PK |
|---|---|---|---|
| log_id | integer |  | PK |
| source_id | character varying(32) |  |  |
| fetched_at | timestamp with time zone |  |  |
| http_status | integer | yes |  |
| status | character varying(32) |  |  |
| content_hash | character varying(64) | yes |  |
| changed | smallint |  |  |
| duration_ms | integer | yes |  |
| error | text | yes |  |
| error_type | character varying(64) | yes |  |

Foreign keys:
- `fetch_log.source_id -> government_sources.source_id`

### gaps

| column | type | nullable | PK |
|---|---|---|---|
| gap_id | uuid |  | PK |
| assessment_id | uuid |  |  |
| field | character varying(128) |  |  |
| priority | character varying(16) |  |  |
| message | text |  |  |
| rule_id | character varying(48) | yes |  |
| evidence_text | text | yes |  |
| answer | text | yes |  |
| source_chunk_ids | json | yes |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

Foreign keys:
- `gaps.assessment_id -> assessments.assessment_id`

### government_documents

| column | type | nullable | PK |
|---|---|---|---|
| document_id | character varying(32) |  | PK |
| source_id | character varying(32) |  |  |
| country_id | character varying(8) |  |  |
| program_id | character varying(32) |  |  |
| visa_category | character varying(32) |  |  |
| document_type | character varying(64) |  |  |
| url | text |  |  |
| title | character varying(512) | yes |  |
| published_date | character varying(32) | yes |  |
| effective_date | character varying(32) | yes |  |
| retrieved_at | timestamp with time zone |  |  |
| content_hash | character varying(64) |  |  |
| current_version | integer |  |  |
| language | character varying(8) |  |  |
| raw_path | text |  |  |
| extracted_path | text | yes |  |
| status | character varying(32) |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

Foreign keys:
- `government_documents.country_id -> countries.country_id`
- `government_documents.country_id -> immigration_programs.country_id`
- `government_documents.country_id -> immigration_programs.program_id`
- `government_documents.program_id -> immigration_programs.country_id`
- `government_documents.program_id -> immigration_programs.program_id`
- `government_documents.program_id -> immigration_programs.program_id`
- `government_documents.source_id -> government_sources.source_id`

### government_sources

| column | type | nullable | PK |
|---|---|---|---|
| source_id | character varying(32) |  | PK |
| country_id | character varying(8) |  |  |
| program_id | character varying(32) |  |  |
| visa_category | character varying(32) |  |  |
| authority | character varying(128) |  |  |
| url | text |  |  |
| source_type | character varying(8) |  |  |
| source_subtype | character varying(64) | yes |  |
| priority | character varying(16) |  |  |
| monitoring_method | character varying(16) |  |  |
| crawl_frequency | character varying(16) |  |  |
| last_checked_at | timestamp with time zone | yes |  |
| last_hash | character varying(64) | yes |  |
| status | character varying(32) |  |  |
| notes | text | yes |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| last_successful_fetch_at | timestamp with time zone | yes |  |
| fallback_source_id | character varying(32) | yes |  |
| fetch_status | character varying(32) | yes |  |
| http_status | integer | yes |  |
| last_error | text | yes |  |
| consecutive_failures | integer |  |  |
| last_error_type | character varying(64) | yes |  |

Foreign keys:
- `government_sources.country_id -> countries.country_id`
- `government_sources.country_id -> immigration_programs.country_id`
- `government_sources.country_id -> immigration_programs.program_id`
- `government_sources.fallback_source_id -> government_sources.source_id`
- `government_sources.program_id -> immigration_programs.country_id`
- `government_sources.program_id -> immigration_programs.program_id`
- `government_sources.program_id -> immigration_programs.program_id`

### immigration_programs

| column | type | nullable | PK |
|---|---|---|---|
| program_id | character varying(32) |  | PK |
| country_id | character varying(8) |  |  |
| visa_category | character varying(32) |  |  |
| subclass_code | character varying(8) | yes |  |
| name | character varying(256) |  |  |
| status | character varying(16) |  |  |

Foreign keys:
- `immigration_programs.country_id -> countries.country_id`

### llm_budget_log

| column | type | nullable | PK |
|---|---|---|---|
| log_id | uuid |  | PK |
| period | character varying(16) |  |  |
| period_start | timestamp with time zone |  |  |
| provider | character varying(32) | yes |  |
| total_cost_usd | numeric |  |  |
| total_calls | integer |  |  |
| total_tokens | integer |  |  |
| budget_limit_usd | numeric | yes |  |
| exceeded | boolean |  |  |

### llm_usage

| column | type | nullable | PK |
|---|---|---|---|
| usage_id | uuid |  | PK |
| request_id | character varying(36) | yes |  |
| user_id | uuid | yes |  |
| provider | character varying(32) |  |  |
| model | character varying(64) |  |  |
| prompt_tokens | integer |  |  |
| completion_tokens | integer |  |  |
| total_tokens | integer |  |  |
| cost_usd | numeric | yes |  |
| latency_ms | integer | yes |  |
| success | boolean |  |  |
| error_message | text | yes |  |
| created_at | timestamp with time zone |  |  |

Foreign keys:
- `llm_usage.user_id -> api_users.user_id`

### pipeline_runs

| column | type | nullable | PK |
|---|---|---|---|
| run_id | character varying(36) |  | PK |
| source_id | character varying(32) |  |  |
| trigger_type | character varying(16) |  |  |
| status | character varying(16) |  |  |
| started_at | timestamp with time zone |  |  |
| completed_at | timestamp with time zone | yes |  |
| duration_ms | integer | yes |  |
| old_hash | character varying(64) | yes |  |
| new_hash | character varying(64) | yes |  |
| requirements_extracted | integer |  |  |
| requirements_approved | integer |  |  |
| rules_created | integer |  |  |
| rules_validated | integer |  |  |
| rules_approved | integer |  |  |
| rules_activated | integer |  |  |
| errors | json | yes |  |
| metadata_json | json | yes |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

Foreign keys:
- `pipeline_runs.source_id -> government_sources.source_id`

### requirement_evidence

| column | type | nullable | PK |
|---|---|---|---|
| evidence_id | integer |  | PK |
| requirement_id | character varying(48) |  |  |
| document_id | character varying(32) |  |  |
| document_version | integer |  |  |
| chunk_id | integer |  |  |
| evidence_text | text |  |  |
| evidence_type | character varying(32) |  |  |
| verified | smallint |  |  |
| verification_note | text | yes |  |
| extraction_method | character varying(32) |  |  |
| extracted_at | timestamp with time zone |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| document_version_id | integer | yes |  |

Foreign keys:
- `requirement_evidence.chunk_id -> document_chunks.chunk_id`
- `requirement_evidence.chunk_id -> document_chunks.chunk_id`
- `requirement_evidence.chunk_id -> document_chunks.document_id`
- `requirement_evidence.chunk_id -> document_chunks.version_id`
- `requirement_evidence.document_id -> document_chunks.chunk_id`
- `requirement_evidence.document_id -> document_chunks.document_id`
- `requirement_evidence.document_id -> document_chunks.version_id`
- `requirement_evidence.document_id -> document_versions.document_id`
- `requirement_evidence.document_id -> document_versions.version_number`
- `requirement_evidence.document_id -> government_documents.document_id`
- `requirement_evidence.document_version -> document_versions.document_id`
- `requirement_evidence.document_version -> document_versions.version_number`
- `requirement_evidence.document_version_id -> document_chunks.chunk_id`
- `requirement_evidence.document_version_id -> document_chunks.document_id`
- `requirement_evidence.document_version_id -> document_chunks.version_id`
- `requirement_evidence.document_version_id -> document_versions.version_id`
- `requirement_evidence.requirement_id -> requirements.requirement_id`

### requirement_references

| column | type | nullable | PK |
|---|---|---|---|
| reference_id | integer |  | PK |
| requirement_id | character varying(48) |  |  |
| reference_type | character varying(32) |  |  |
| target_text | text |  |  |
| resolved_chunk_id | integer | yes |  |
| status | character varying(32) |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

Foreign keys:
- `requirement_references.requirement_id -> requirements.requirement_id`
- `requirement_references.resolved_chunk_id -> document_chunks.chunk_id`

### requirement_review

| column | type | nullable | PK |
|---|---|---|---|
| review_id | integer |  | PK |
| requirement_id | character varying(48) |  |  |
| reviewer | character varying(64) |  |  |
| action | character varying(32) |  |  |
| old_state_json | text | yes |  |
| new_state_json | text | yes |  |
| notes | text | yes |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

Foreign keys:
- `requirement_review.requirement_id -> requirements.requirement_id`

### requirement_versions

| column | type | nullable | PK |
|---|---|---|---|
| version_id | integer |  | PK |
| requirement_id | character varying(48) |  |  |
| version_number | integer |  |  |
| snapshot_json | text |  |  |
| document_version | integer |  |  |
| change_type | character varying(16) |  |  |
| created_by | character varying(64) |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| document_version_id | integer | yes |  |

Foreign keys:
- `requirement_versions.document_version_id -> document_versions.version_id`
- `requirement_versions.requirement_id -> requirements.requirement_id`

### requirements

| column | type | nullable | PK |
|---|---|---|---|
| requirement_id | character varying(48) |  | PK |
| semantic_key | character varying(256) |  |  |
| program_id | character varying(32) |  |  |
| country_id | character varying(8) |  |  |
| category | character varying(64) |  |  |
| name | character varying(256) |  |  |
| description | text | yes |  |
| field | character varying(128) | yes |  |
| operator | character varying(16) |  |  |
| value_json | text | yes |  |
| unit | character varying(32) | yes |  |
| mandatory | smallint | yes |  |
| condition_json | text | yes |  |
| exception_json | text | yes |  |
| applies_to | text | yes |  |
| content_kind | character varying(32) |  |  |
| confidence | double precision | yes |  |
| criticality | character varying(32) |  |  |
| status | character varying(32) |  |  |
| current_version | integer |  |  |
| extraction_run_id | character varying(24) |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| document_version_id | integer | yes |  |

Foreign keys:
- `requirements.country_id -> countries.country_id`
- `requirements.country_id -> immigration_programs.country_id`
- `requirements.country_id -> immigration_programs.program_id`
- `requirements.document_version_id -> document_versions.version_id`
- `requirements.extraction_run_id -> extraction_runs.run_id`
- `requirements.program_id -> immigration_programs.country_id`
- `requirements.program_id -> immigration_programs.program_id`
- `requirements.program_id -> immigration_programs.program_id`

### rule_versions

| column | type | nullable | PK |
|---|---|---|---|
| version_id | integer |  | PK |
| rule_id | character varying(48) |  |  |
| version_number | integer |  |  |
| snapshot_json | json |  |  |
| change_type | character varying(16) |  |  |
| created_by | character varying(64) |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| requirement_version_id | integer | yes |  |

Foreign keys:
- `rule_versions.requirement_version_id -> requirement_versions.version_id`
- `rule_versions.rule_id -> rules.rule_id`

### rules

| column | type | nullable | PK |
|---|---|---|---|
| rule_id | character varying(48) |  | PK |
| requirement_id | character varying(48) |  |  |
| program_id | character varying(32) |  |  |
| category | character varying(64) |  |  |
| field | character varying(128) |  |  |
| operator | character varying(16) |  |  |
| value_json | text | yes |  |
| unit | character varying(32) | yes |  |
| mandatory | boolean |  |  |
| critical | boolean |  |  |
| message | text |  |  |
| status | character varying(16) |  |  |
| current_version | integer |  |  |
| expression_json | json | yes |  |
| condition_json | json | yes |  |
| exception_json | json | yes |  |
| source_id | character varying(32) | yes |  |
| document_id | character varying(32) | yes |  |
| document_version | integer | yes |  |
| chunk_id | integer | yes |  |
| evidence_text | text | yes |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| requirement_version_id | integer | yes |  |

Foreign keys:
- `rules.program_id -> immigration_programs.program_id`
- `rules.program_id -> requirements.program_id`
- `rules.program_id -> requirements.requirement_id`
- `rules.requirement_id -> requirements.program_id`
- `rules.requirement_id -> requirements.requirement_id`
- `rules.requirement_id -> requirements.requirement_id`
- `rules.requirement_version_id -> requirement_versions.version_id`

### ruleset_rules

| column | type | nullable | PK |
|---|---|---|---|
| ruleset_id | character varying(36) |  | PK |
| rule_version_id | integer |  | PK |
| position | integer |  |  |

Foreign keys:
- `ruleset_rules.rule_version_id -> rule_versions.version_id`
- `ruleset_rules.ruleset_id -> rulesets.ruleset_id`

### rulesets

| column | type | nullable | PK |
|---|---|---|---|
| ruleset_id | character varying(36) |  | PK |
| program_id | character varying(32) |  |  |
| content_hash | character varying(64) |  |  |
| status | character varying(16) |  |  |
| released_at | timestamp with time zone |  |  |
| released_by | character varying(64) |  |  |
| rule_count | integer |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

Foreign keys:
- `rulesets.program_id -> immigration_programs.program_id`

### user_sessions

| column | type | nullable | PK |
|---|---|---|---|
| session_id | uuid |  | PK |
| user_id | uuid |  |  |
| token_hash | character varying(64) |  |  |
| expires_at | timestamp with time zone |  |  |
| revoked | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

Foreign keys:
- `user_sessions.user_id -> api_users.user_id`

### visa_selections

| column | type | nullable | PK |
|---|---|---|---|
| selection_id | uuid |  | PK |
| applicant_id | uuid |  |  |
| program_id | character varying(32) |  |  |
| selected_at | timestamp with time zone |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

Foreign keys:
- `visa_selections.applicant_id -> applicant_profiles.applicant_id`
- `visa_selections.program_id -> immigration_programs.program_id`

## Foreign keys — full list

- `applicant_profiles.user_id -> api_users.user_id`
- `assessment_rule_results.assessment_id -> assessments.assessment_id`
- `assessment_rule_results.rule_id -> rules.rule_id`
- `assessment_rule_results.rule_version_id -> rule_versions.version_id`
- `assessments.applicant_id -> applicant_profiles.applicant_id`
- `assessments.program_id -> immigration_programs.program_id`
- `assessments.ruleset_id -> rulesets.ruleset_id`
- `audit_log.user_id -> api_users.user_id`
- `discovered_sources.suggested_country_id -> countries.country_id`
- `document_chunks.document_id -> document_versions.document_id`
- `document_chunks.document_id -> document_versions.version_id`
- `document_chunks.document_id -> government_documents.document_id`
- `document_chunks.version_id -> document_versions.document_id`
- `document_chunks.version_id -> document_versions.version_id`
- `document_chunks.version_id -> document_versions.version_id`
- `document_versions.document_id -> government_documents.document_id`
- `extraction_runs.document_id -> document_versions.document_id`
- `extraction_runs.document_id -> document_versions.version_number`
- `extraction_runs.document_id -> government_documents.document_id`
- `extraction_runs.document_version -> document_versions.document_id`
- `extraction_runs.document_version -> document_versions.version_number`
- `extraction_runs.document_version_id -> document_versions.version_id`
- `fetch_log.source_id -> government_sources.source_id`
- `gaps.assessment_id -> assessments.assessment_id`
- `government_documents.country_id -> countries.country_id`
- `government_documents.country_id -> immigration_programs.country_id`
- `government_documents.country_id -> immigration_programs.program_id`
- `government_documents.program_id -> immigration_programs.country_id`
- `government_documents.program_id -> immigration_programs.program_id`
- `government_documents.program_id -> immigration_programs.program_id`
- `government_documents.source_id -> government_sources.source_id`
- `government_sources.country_id -> countries.country_id`
- `government_sources.country_id -> immigration_programs.country_id`
- `government_sources.country_id -> immigration_programs.program_id`
- `government_sources.fallback_source_id -> government_sources.source_id`
- `government_sources.program_id -> immigration_programs.country_id`
- `government_sources.program_id -> immigration_programs.program_id`
- `government_sources.program_id -> immigration_programs.program_id`
- `immigration_programs.country_id -> countries.country_id`
- `llm_usage.user_id -> api_users.user_id`
- `pipeline_runs.source_id -> government_sources.source_id`
- `requirement_evidence.chunk_id -> document_chunks.chunk_id`
- `requirement_evidence.chunk_id -> document_chunks.chunk_id`
- `requirement_evidence.chunk_id -> document_chunks.document_id`
- `requirement_evidence.chunk_id -> document_chunks.version_id`
- `requirement_evidence.document_id -> document_chunks.chunk_id`
- `requirement_evidence.document_id -> document_chunks.document_id`
- `requirement_evidence.document_id -> document_chunks.version_id`
- `requirement_evidence.document_id -> document_versions.document_id`
- `requirement_evidence.document_id -> document_versions.version_number`
- `requirement_evidence.document_id -> government_documents.document_id`
- `requirement_evidence.document_version -> document_versions.document_id`
- `requirement_evidence.document_version -> document_versions.version_number`
- `requirement_evidence.document_version_id -> document_chunks.chunk_id`
- `requirement_evidence.document_version_id -> document_chunks.document_id`
- `requirement_evidence.document_version_id -> document_chunks.version_id`
- `requirement_evidence.document_version_id -> document_versions.version_id`
- `requirement_evidence.requirement_id -> requirements.requirement_id`
- `requirement_references.requirement_id -> requirements.requirement_id`
- `requirement_references.resolved_chunk_id -> document_chunks.chunk_id`
- `requirement_review.requirement_id -> requirements.requirement_id`
- `requirement_versions.document_version_id -> document_versions.version_id`
- `requirement_versions.requirement_id -> requirements.requirement_id`
- `requirements.country_id -> countries.country_id`
- `requirements.country_id -> immigration_programs.country_id`
- `requirements.country_id -> immigration_programs.program_id`
- `requirements.document_version_id -> document_versions.version_id`
- `requirements.extraction_run_id -> extraction_runs.run_id`
- `requirements.program_id -> immigration_programs.country_id`
- `requirements.program_id -> immigration_programs.program_id`
- `requirements.program_id -> immigration_programs.program_id`
- `rule_versions.requirement_version_id -> requirement_versions.version_id`
- `rule_versions.rule_id -> rules.rule_id`
- `rules.program_id -> immigration_programs.program_id`
- `rules.program_id -> requirements.program_id`
- `rules.program_id -> requirements.requirement_id`
- `rules.requirement_id -> requirements.program_id`
- `rules.requirement_id -> requirements.requirement_id`
- `rules.requirement_id -> requirements.requirement_id`
- `rules.requirement_version_id -> requirement_versions.version_id`
- `ruleset_rules.rule_version_id -> rule_versions.version_id`
- `ruleset_rules.ruleset_id -> rulesets.ruleset_id`
- `rulesets.program_id -> immigration_programs.program_id`
- `user_sessions.user_id -> api_users.user_id`
- `visa_selections.applicant_id -> applicant_profiles.applicant_id`
- `visa_selections.program_id -> immigration_programs.program_id`

