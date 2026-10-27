# Database Structure

**Regenerated**: 2026-10-27 with `python scripts/db_structure.py` from a
live PostgreSQL 16 + pgvector database at the current alembic head. The table and column
lists are introspected, not hand-written; row counts are whatever that database held at
generation time. Re-run the script after a migration instead of editing this file.

- Alembic head: `e5f6a7b8c9d0`
- Application tables: 33
- `document_chunks.embedding` is pgvector's `vector(1536)`; SQLAlchemy's stock
  inspector reports it as an unknown `NULL` type, so it is labelled explicitly here.

## Migrations (applied in order)

| # | Revision | File | Purpose |
|---|---|---|---|
| 1 | `49b9088f3a03` | `49b9088f3a03_initial_schema_from_models.py` | Initial schema from the ORM models. |
| 2 | `a1b2c3d4e5f6` | `a1b2c3d4e5f6_add_last_successful_fetch_at.py` | `government_sources.last_successful_fetch_at`. |
| 3 | `c7d8e9f0a1b2` | `c7d8e9f0a1b2_add_discovered_sources.py` | `discovered_sources` table (source auto-discovery candidates). |
| 4 | `d1f2e3a4b5c6` | `d1f2e3a4b5c6_add_fallback_fields_to_government_sources.py` | Fallback strategy fields on `government_sources`. |
| 5 | `0ceaaa268719` | `0ceaaa268719_schema_verification.py` | Schema verification (constraints, indexes, triggers). |
| 6 | `f7a6d5c4b3a2` | `f7a6d5c4b3a2_lineage_and_immutability.py` | Lineage, append-only triggers and integrity hardening. |
| 7 | `f8a9b0c1d2e3` | `f8a9b0c1d2e3_audit_hardening.py` | Audit hardening follow-up (constraints and indexes). |
| 8 | `e4f6a1b2c3d4` | `e4f6a1b2c3d4_add_fetch_error_type.py` | Explicit fetch-failure classification (`fetch_log.error_type`). |
| 9 | `a3c5e7f9b1d3` | `a3c5e7f9b1d3_widen_requirement_id_columns.py` | Widen `requirement_id`/`rule_id` to VARCHAR(96) to stop silent truncation. |
| 10 | `7c9e2a4f1b6d` | `7c9e2a4f1b6d_widen_active_rule_uniqueness_to_include_category.py` | Widen ACTIVE-rule uniqueness to (program, category, field). |
| 11 | `112f70d87c01` | `112f70d87c01_add_deferred_chunks_and_partial_status.py` | `extraction_runs.deferred_chunks` and the PARTIAL status. |
| 12 | `0dbea9e27e35` | `0dbea9e27e35_add_scheduler_settings.py` | `scheduler_settings` (admin-editable schedule override). |
| 13 | `b7c8d9e0f1a2` | `b7c8d9e0f1a2_add_host_cooldowns.py` | `host_cooldowns` (pause a host after a 403/429 refusal). |
| 14 | `c1d2e3f4a5b6` | `c1d2e3f4a5b6_add_conditional_get_validators.py` | Conditional-GET validators (`etag`, `last_modified`). |
| 15 | `d2e3f4a5b6c7` | `d2e3f4a5b6c7_add_requirement_concept.py` | `requirements.concept` (closed vocabulary, NOT_MODELED label). |
| 16 | `e3f4a5b6c7d8` | `e3f4a5b6c7d8_add_extract_requirements_scope_flag.py` | `government_sources.extract_requirements` scope flag (reference-only sources). |
| 17 | `f4a5b6c7d8e9` | `f4a5b6c7d8e9_add_extraction_cache.py` | Persistent `extraction_cache` for chunk-level LLM results. |
| 18 | `a5b6c7d8e9f0` | `a5b6c7d8e9f0_add_extraction_run_stored_new_metrics.py` | `extraction_runs.stored_new`/`merged_into_existing` metrics. |
| 19 | `b6c7d8e9f0a1` | `b6c7d8e9f0a1_add_pipeline_run_stored_new.py` | `pipeline_runs.stored_new` metric. |
| 20 | `c7d8e9f0a1b3` | `c7d8e9f0a1b3_add_source_content_kind.py` | `government_sources.content_kind` (PAGE \| NEWS_LIST). |
| 21 | `d8e9f0a1b2c4` | `d8e9f0a1b2c4_allow_ok_empty_content_fetch_status.py` | Allow `fetch_status = OK_EMPTY_CONTENT`. |
| 22 | `b1c2d3e4f5a6` | `b1c2d3e4f5a6_add_applicant_concept_fields.py` | First batch of modelled applicant concept fields. |
| 23 | `c2d3e4f5a6b7` | `c2d3e4f5a6b7_add_chunk_embeddings.py` | pgvector `document_chunks.embedding` + HNSW cosine index. |
| 24 | `d3e4f5a6b7c8` | `d3e4f5a6b7c8_add_fetch_retrieval_events.py` | Fetch retrieval events (`fetch_log.retrieval_id`, URL/content/timing fields). |
| 25 | `e5f6a7b8c9d0` **(head)** | `e5f6a7b8c9d0_retire_needs_manual_capture.py` | Retire `NEEDS_MANUAL_CAPTURE`; existing rows become `ACTIVE`. |

## Tables

| Table | Columns | Rows at generation |
|---|---|---|
| alembic_version | 1 | 1 |
| api_users | 9 | 4 |
| applicant_profiles | 25 | 2 |
| assessment_rule_results | 14 | 1 |
| assessments | 17 | 1 |
| audit_log | 10 | 0 |
| countries | 3 | 1 |
| discovered_sources | 16 | 0 |
| document_chunks | 11 | 775 |
| document_versions | 11 | 4 |
| extraction_cache | 7 | 0 |
| extraction_runs | 24 | 5 |
| fetch_log | 19 | 7 |
| gaps | 11 | 1 |
| government_documents | 19 | 3 |
| government_sources | 28 | 14 |
| host_cooldowns | 7 | 0 |
| immigration_programs | 6 | 7 |
| llm_budget_log | 9 | 0 |
| llm_usage | 13 | 0 |
| pipeline_runs | 20 | 7 |
| requirement_evidence | 14 | 892 |
| requirement_references | 8 | 40 |
| requirement_review | 9 | 8 |
| requirement_versions | 10 | 652 |
| requirements | 25 | 653 |
| rule_versions | 9 | 5 |
| rules | 24 | 2 |
| ruleset_rules | 3 | 1 |
| rulesets | 9 | 1 |
| scheduler_settings | 9 | 1 |
| user_sessions | 7 | 0 |
| visa_selections | 6 | 0 |

## Keys and indexes

Generated primary-key indexes (`*_pkey`, `alembic_version_pkc`) and plain
single/multi-column btree indexes are omitted; every table has a primary key, and
the btree indexes are visible in the migrations. Listed here: unique constraints,
partial (`WHERE`) indexes and vector (HNSW) indexes.

| Table | Index | Definition |
|---|---|---|
| alembic_version | `alembic_version_pkc` | `CREATE UNIQUE INDEX alembic_version_pkc ON public.alembic_version USING btree (version_num)` |
| api_users | `api_users_email_key` | `CREATE UNIQUE INDEX api_users_email_key ON public.api_users USING btree (email)` |
| api_users | `api_users_pkey` | `CREATE UNIQUE INDEX api_users_pkey ON public.api_users USING btree (user_id)` |
| applicant_profiles | `applicant_profiles_pkey` | `CREATE UNIQUE INDEX applicant_profiles_pkey ON public.applicant_profiles USING btree (applicant_id)` |
| assessment_rule_results | `assessment_rule_results_pkey` | `CREATE UNIQUE INDEX assessment_rule_results_pkey ON public.assessment_rule_results USING btree (result_id)` |
| assessment_rule_results | `uq_result_assessment_rule` | `CREATE UNIQUE INDEX uq_result_assessment_rule ON public.assessment_rule_results USING btree (assessment_id, rule_id)` |
| assessments | `assessments_pkey` | `CREATE UNIQUE INDEX assessments_pkey ON public.assessments USING btree (assessment_id)` |
| audit_log | `audit_log_pkey` | `CREATE UNIQUE INDEX audit_log_pkey ON public.audit_log USING btree (audit_id)` |
| countries | `countries_pkey` | `CREATE UNIQUE INDEX countries_pkey ON public.countries USING btree (country_id)` |
| discovered_sources | `discovered_sources_candidate_url_key` | `CREATE UNIQUE INDEX discovered_sources_candidate_url_key ON public.discovered_sources USING btree (candidate_url)` |
| discovered_sources | `discovered_sources_pkey` | `CREATE UNIQUE INDEX discovered_sources_pkey ON public.discovered_sources USING btree (discovered_source_id)` |
| document_chunks | `document_chunks_pkey` | `CREATE UNIQUE INDEX document_chunks_pkey ON public.document_chunks USING btree (chunk_id)` |
| document_chunks | `idx_chunks_embedding_hnsw` | `CREATE INDEX idx_chunks_embedding_hnsw ON public.document_chunks USING hnsw (embedding vector_cosine_ops)` |
| document_chunks | `uq_chunk_lineage` | `CREATE UNIQUE INDEX uq_chunk_lineage ON public.document_chunks USING btree (chunk_id, document_id, version_id)` |
| document_chunks | `uq_chunk_version_order` | `CREATE UNIQUE INDEX uq_chunk_version_order ON public.document_chunks USING btree (version_id, order_index)` |
| document_versions | `document_versions_pkey` | `CREATE UNIQUE INDEX document_versions_pkey ON public.document_versions USING btree (version_id)` |
| document_versions | `uq_doc_ver_content` | `CREATE UNIQUE INDEX uq_doc_ver_content ON public.document_versions USING btree (document_id, content_hash)` |
| document_versions | `uq_doc_ver_docid_versionid` | `CREATE UNIQUE INDEX uq_doc_ver_docid_versionid ON public.document_versions USING btree (document_id, version_id)` |
| document_versions | `uq_doc_version` | `CREATE UNIQUE INDEX uq_doc_version ON public.document_versions USING btree (document_id, version_number)` |
| extraction_cache | `extraction_cache_pkey` | `CREATE UNIQUE INDEX extraction_cache_pkey ON public.extraction_cache USING btree (chunk_content_hash, prompt_version, model)` |
| extraction_runs | `extraction_runs_pkey` | `CREATE UNIQUE INDEX extraction_runs_pkey ON public.extraction_runs USING btree (run_id)` |
| fetch_log | `fetch_log_pkey` | `CREATE UNIQUE INDEX fetch_log_pkey ON public.fetch_log USING btree (log_id)` |
| fetch_log | `uq_fetch_log_retrieval_id` | `CREATE UNIQUE INDEX uq_fetch_log_retrieval_id ON public.fetch_log USING btree (retrieval_id)` |
| gaps | `gaps_pkey` | `CREATE UNIQUE INDEX gaps_pkey ON public.gaps USING btree (gap_id)` |
| government_documents | `government_documents_pkey` | `CREATE UNIQUE INDEX government_documents_pkey ON public.government_documents USING btree (document_id)` |
| government_sources | `government_sources_pkey` | `CREATE UNIQUE INDEX government_sources_pkey ON public.government_sources USING btree (source_id)` |
| government_sources | `government_sources_url_key` | `CREATE UNIQUE INDEX government_sources_url_key ON public.government_sources USING btree (url)` |
| host_cooldowns | `host_cooldowns_pkey` | `CREATE UNIQUE INDEX host_cooldowns_pkey ON public.host_cooldowns USING btree (host)` |
| immigration_programs | `immigration_programs_pkey` | `CREATE UNIQUE INDEX immigration_programs_pkey ON public.immigration_programs USING btree (program_id)` |
| immigration_programs | `uq_gov_src_program_country` | `CREATE UNIQUE INDEX uq_gov_src_program_country ON public.immigration_programs USING btree (program_id, country_id)` |
| llm_budget_log | `llm_budget_log_pkey` | `CREATE UNIQUE INDEX llm_budget_log_pkey ON public.llm_budget_log USING btree (log_id)` |
| llm_usage | `llm_usage_pkey` | `CREATE UNIQUE INDEX llm_usage_pkey ON public.llm_usage USING btree (usage_id)` |
| pipeline_runs | `pipeline_runs_pkey` | `CREATE UNIQUE INDEX pipeline_runs_pkey ON public.pipeline_runs USING btree (run_id)` |
| requirement_evidence | `requirement_evidence_pkey` | `CREATE UNIQUE INDEX requirement_evidence_pkey ON public.requirement_evidence USING btree (evidence_id)` |
| requirement_references | `requirement_references_pkey` | `CREATE UNIQUE INDEX requirement_references_pkey ON public.requirement_references USING btree (reference_id)` |
| requirement_review | `requirement_review_pkey` | `CREATE UNIQUE INDEX requirement_review_pkey ON public.requirement_review USING btree (review_id)` |
| requirement_versions | `requirement_versions_pkey` | `CREATE UNIQUE INDEX requirement_versions_pkey ON public.requirement_versions USING btree (version_id)` |
| requirement_versions | `uq_req_version` | `CREATE UNIQUE INDEX uq_req_version ON public.requirement_versions USING btree (requirement_id, version_number)` |
| requirements | `requirements_pkey` | `CREATE UNIQUE INDEX requirements_pkey ON public.requirements USING btree (requirement_id)` |
| requirements | `uq_req_active_semantic` | `CREATE UNIQUE INDEX uq_req_active_semantic ON public.requirements USING btree (program_id, semantic_key) WHERE ((status)::text <> ALL ((ARRAY['CONFLICT'::character varying, 'REJECTED'::character varying])::text[]))` |
| requirements | `uq_req_program_pair` | `CREATE UNIQUE INDEX uq_req_program_pair ON public.requirements USING btree (requirement_id, program_id)` |
| rule_versions | `rule_versions_pkey` | `CREATE UNIQUE INDEX rule_versions_pkey ON public.rule_versions USING btree (version_id)` |
| rule_versions | `uq_rule_version` | `CREATE UNIQUE INDEX uq_rule_version ON public.rule_versions USING btree (rule_id, version_number)` |
| rules | `rules_pkey` | `CREATE UNIQUE INDEX rules_pkey ON public.rules USING btree (rule_id)` |
| rules | `uq_rules_active_per_field` | `CREATE UNIQUE INDEX uq_rules_active_per_field ON public.rules USING btree (program_id, category, field) WHERE ((status)::text = 'ACTIVE'::text)` |
| ruleset_rules | `ruleset_rules_pkey` | `CREATE UNIQUE INDEX ruleset_rules_pkey ON public.ruleset_rules USING btree (ruleset_id, rule_version_id)` |
| ruleset_rules | `uq_ruleset_rule_position` | `CREATE UNIQUE INDEX uq_ruleset_rule_position ON public.ruleset_rules USING btree (ruleset_id, "position")` |
| rulesets | `rulesets_pkey` | `CREATE UNIQUE INDEX rulesets_pkey ON public.rulesets USING btree (ruleset_id)` |
| rulesets | `uq_ruleset_content` | `CREATE UNIQUE INDEX uq_ruleset_content ON public.rulesets USING btree (program_id, content_hash)` |
| scheduler_settings | `scheduler_settings_pkey` | `CREATE UNIQUE INDEX scheduler_settings_pkey ON public.scheduler_settings USING btree (id)` |
| user_sessions | `user_sessions_pkey` | `CREATE UNIQUE INDEX user_sessions_pkey ON public.user_sessions USING btree (session_id)` |
| visa_selections | `uq_applicant_program` | `CREATE UNIQUE INDEX uq_applicant_program ON public.visa_selections USING btree (applicant_id, program_id)` |
| visa_selections | `visa_selections_pkey` | `CREATE UNIQUE INDEX visa_selections_pkey ON public.visa_selections USING btree (selection_id)` |

## Foreign keys

| Table | Column | References | On delete |
|---|---|---|---|
| applicant_profiles | user_id | api_users(user_id) |  |
| assessment_rule_results | assessment_id | assessments(assessment_id) |  |
| assessment_rule_results | rule_id | rules(rule_id) |  |
| assessment_rule_results | rule_version_id | rule_versions(version_id) |  |
| assessments | applicant_id | applicant_profiles(applicant_id) |  |
| assessments | program_id | immigration_programs(program_id) |  |
| assessments | ruleset_id | rulesets(ruleset_id) |  |
| audit_log | user_id | api_users(user_id) |  |
| discovered_sources | suggested_country_id | countries(country_id) |  |
| document_chunks | document_id | government_documents(document_id) |  |
| document_chunks | version_id | document_versions(version_id) |  |
| document_chunks | document_id, version_id | document_versions(document_id, version_id) |  |
| document_versions | document_id | government_documents(document_id) |  |
| extraction_runs | document_id | government_documents(document_id) |  |
| extraction_runs | document_id, document_version | document_versions(document_id, version_number) |  |
| extraction_runs | document_version_id | document_versions(version_id) |  |
| fetch_log | source_id | government_sources(source_id) |  |
| gaps | assessment_id | assessments(assessment_id) |  |
| government_documents | program_id, country_id | immigration_programs(program_id, country_id) |  |
| government_documents | country_id | countries(country_id) |  |
| government_documents | program_id | immigration_programs(program_id) |  |
| government_documents | source_id | government_sources(source_id) |  |
| government_sources | fallback_source_id | government_sources(source_id) |  |
| government_sources | program_id, country_id | immigration_programs(program_id, country_id) |  |
| government_sources | country_id | countries(country_id) |  |
| government_sources | program_id | immigration_programs(program_id) |  |
| host_cooldowns | source_id | government_sources(source_id) | CASCADE |
| immigration_programs | country_id | countries(country_id) |  |
| llm_usage | user_id | api_users(user_id) |  |
| pipeline_runs | source_id | government_sources(source_id) |  |
| requirement_evidence | chunk_id, document_id, document_version_id | document_chunks(chunk_id, document_id, version_id) |  |
| requirement_evidence | document_id, document_version | document_versions(document_id, version_number) |  |
| requirement_evidence | document_version_id | document_versions(version_id) |  |
| requirement_evidence | chunk_id | document_chunks(chunk_id) |  |
| requirement_evidence | document_id | government_documents(document_id) |  |
| requirement_evidence | requirement_id | requirements(requirement_id) |  |
| requirement_references | requirement_id | requirements(requirement_id) |  |
| requirement_references | resolved_chunk_id | document_chunks(chunk_id) |  |
| requirement_review | requirement_id | requirements(requirement_id) |  |
| requirement_versions | document_version_id | document_versions(version_id) |  |
| requirement_versions | requirement_id | requirements(requirement_id) |  |
| requirements | program_id, country_id | immigration_programs(program_id, country_id) |  |
| requirements | document_version_id | document_versions(version_id) |  |
| requirements | country_id | countries(country_id) |  |
| requirements | extraction_run_id | extraction_runs(run_id) |  |
| requirements | program_id | immigration_programs(program_id) |  |
| rule_versions | requirement_version_id | requirement_versions(version_id) |  |
| rule_versions | rule_id | rules(rule_id) |  |
| rules | requirement_id, program_id | requirements(requirement_id, program_id) |  |
| rules | requirement_version_id | requirement_versions(version_id) |  |
| rules | program_id | immigration_programs(program_id) |  |
| rules | requirement_id | requirements(requirement_id) |  |
| ruleset_rules | rule_version_id | rule_versions(version_id) |  |
| ruleset_rules | ruleset_id | rulesets(ruleset_id) |  |
| rulesets | program_id | immigration_programs(program_id) |  |
| user_sessions | user_id | api_users(user_id) |  |
| visa_selections | applicant_id | applicant_profiles(applicant_id) |  |
| visa_selections | program_id | immigration_programs(program_id) |  |

## Append-only triggers

These triggers raise on UPDATE/DELETE, so historical assessments and rule versions cannot
be rewritten. The only supported way to bypass them is the test/cleanup pattern of
`ALTER TABLE ... DISABLE TRIGGER ALL` inside an explicit maintenance transaction.

| Table | Trigger | Timing / events |
|---|---|---|
| assessment_rule_results | `trg_assessment_rule_results_append_only` | BEFORE DELETE, BEFORE UPDATE |
| assessments | `trg_assessments_append_only` | BEFORE DELETE, BEFORE UPDATE |
| document_versions | `trg_document_versions_append_only` | BEFORE DELETE, BEFORE UPDATE |
| government_sources | `trg_government_sources_no_self_fallback` | BEFORE INSERT, BEFORE UPDATE |
| requirement_versions | `trg_requirement_versions_append_only` | BEFORE DELETE, BEFORE UPDATE |
| rule_versions | `trg_rule_versions_append_only` | BEFORE DELETE, BEFORE UPDATE |
| ruleset_rules | `trg_ruleset_rules_append_only` | BEFORE DELETE, BEFORE UPDATE |
| rulesets | `trg_rulesets_append_only` | BEFORE DELETE, BEFORE UPDATE |

## Column detail

### alembic_version

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| version_num | varchar(32) |  |  |

### api_users

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| user_id | uuid |  |  |
| email | varchar(256) |  |  |
| password_hash | varchar(256) |  |  |
| full_name | varchar(256) | yes |  |
| role | varchar(32) |  |  |
| is_active | boolean |  |  |
| last_login_at | timestamp | yes |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |

### applicant_profiles

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| applicant_id | uuid |  |  |
| user_id | uuid |  |  |
| first_name | varchar(128) | yes |  |
| last_name | varchar(128) | yes |  |
| date_of_birth | timestamp | yes |  |
| passport_nationality | varchar(8) | yes |  |
| current_visa_subclass | varchar(8) | yes |  |
| profile_json | json |  |  |
| age | integer | yes |  |
| english_level | varchar(32) | yes |  |
| points | integer | yes |  |
| occupation | varchar(32) | yes |  |
| skills_assessment_status | varchar(32) | yes |  |
| health_status | varchar(32) | yes |  |
| character_status | varchar(32) | yes |  |
| has_regional_nomination | boolean | yes |  |
| visa_subclass | varchar(8) | yes |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |
| visa_history | varchar(32) | yes |  |
| employer_sponsored | boolean | yes |  |
| work_experience_years | integer | yes |  |
| in_australia_at_application | boolean | yes |  |
| has_health_insurance | boolean | yes |  |
| genuine_temporary_entrant | boolean | yes |  |

### assessment_rule_results

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| result_id | uuid |  |  |
| assessment_id | uuid |  |  |
| rule_id | varchar(96) |  |  |
| rule_version | integer |  |  |
| status | varchar(16) |  |  |
| field | varchar(128) | yes |  |
| actual_value | text | yes |  |
| expected_value | text | yes |  |
| operator | varchar(16) | yes |  |
| message | text | yes |  |
| reason | text | yes |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |
| rule_version_id | integer | yes |  |

### assessments

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| assessment_id | uuid |  |  |
| applicant_id | uuid |  |  |
| program_id | varchar(32) |  |  |
| business_status | varchar(16) |  |  |
| overall_status | varchar(16) |  |  |
| total_rules | integer |  |  |
| passed_count | integer |  |  |
| failed_count | integer |  |  |
| unknown_count | integer |  |  |
| not_applicable_count | integer |  |  |
| evaluated_at | timestamp |  |  |
| request_id | varchar(36) | yes |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |
| applicant_snapshot | json | yes |  |
| ruleset_hash | varchar(64) | yes |  |
| ruleset_id | varchar(36) | yes |  |

### audit_log

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| audit_id | uuid |  |  |
| user_id | uuid | yes |  |
| action | varchar(64) |  |  |
| entity_type | varchar(64) |  |  |
| entity_id | varchar(64) | yes |  |
| before_json | json | yes |  |
| after_json | json | yes |  |
| request_id | varchar(36) | yes |  |
| ip_address | varchar(64) | yes |  |
| created_at | timestamp |  |  |

### countries

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| country_id | varchar(8) |  |  |
| name | varchar(128) |  |  |
| iso_code | varchar(8) |  |  |

### discovered_sources

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| discovered_source_id | varchar(32) |  |  |
| seed_url | text |  |  |
| candidate_url | text |  |  |
| page_title | varchar(512) | yes |  |
| matched_keywords | json |  |  |
| confidence_score | integer |  |  |
| suggested_visa_category | varchar(32) | yes |  |
| suggested_country_id | varchar(8) |  |  |
| status | varchar(24) |  |  |
| discovered_at | timestamp |  |  |
| discovery_run_id | varchar(36) | yes |  |
| reviewed_by | varchar(36) | yes |  |
| reviewed_at | timestamp | yes |  |
| rejection_reason | text | yes |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |

### document_chunks

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| chunk_id | integer |  |  |
| document_id | varchar(32) |  |  |
| version_id | integer |  |  |
| chunk_type | varchar(16) |  |  |
| heading_path | varchar(512) | yes |  |
| order_index | integer |  |  |
| raw_text | text |  |  |
| page_number | integer | yes |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |
| embedding | vector(1536) | yes |  |

### document_versions

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| version_id | integer |  |  |
| document_id | varchar(32) |  |  |
| version_number | integer |  |  |
| content_hash | varchar(64) |  |  |
| retrieved_at | timestamp |  |  |
| raw_path | text |  |  |
| extracted_path | text | yes |  |
| change_type | varchar(16) |  |  |
| diff_summary | text | yes |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |

### extraction_cache

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| chunk_content_hash | varchar(64) |  |  |
| prompt_version | varchar(64) |  |  |
| model | varchar(64) |  |  |
| source_chunk_id | integer |  |  |
| raw_output | text |  |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |

### extraction_runs

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| run_id | varchar(24) |  |  |
| document_id | varchar(32) |  |  |
| document_version | integer |  |  |
| model | varchar(64) |  |  |
| prompt_version | varchar(64) |  |  |
| chunks_seen | integer |  |  |
| candidate_chunks | integer |  |  |
| llm_calls | integer |  |  |
| requirements_extracted | integer |  |  |
| validated | integer |  |  |
| needs_review | integer |  |  |
| rejected | integer |  |  |
| evidence_failures | integer |  |  |
| schema_failures | integer |  |  |
| status | varchar(16) |  |  |
| dry_run | smallint |  |  |
| error | text | yes |  |
| completed_at | timestamp | yes |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |
| document_version_id | integer | yes |  |
| deferred_chunks | integer |  |  |
| stored_new | integer |  |  |
| merged_into_existing | integer |  |  |

### fetch_log

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| log_id | integer |  |  |
| source_id | varchar(32) |  |  |
| fetched_at | timestamp |  |  |
| http_status | integer | yes |  |
| status | varchar(32) |  |  |
| content_hash | varchar(64) | yes |  |
| changed | smallint |  |  |
| duration_ms | integer | yes |  |
| error | text | yes |  |
| error_type | varchar(64) | yes |  |
| retrieval_id | varchar(36) | yes |  |
| acquisition | varchar(32) | yes |  |
| final_url | varchar(2048) | yes |  |
| content_type | varchar(128) | yes |  |
| limiter_wait_ms | integer | yes |  |
| browser_launch_ms | integer | yes |  |
| goto_ms | integer | yes |  |
| selector_wait_ms | integer | yes |  |
| selector_timed_out | boolean | yes |  |

### gaps

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| gap_id | uuid |  |  |
| assessment_id | uuid |  |  |
| field | varchar(128) |  |  |
| priority | varchar(16) |  |  |
| message | text |  |  |
| rule_id | varchar(96) | yes |  |
| evidence_text | text | yes |  |
| answer | text | yes |  |
| source_chunk_ids | json | yes |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |

### government_documents

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| document_id | varchar(32) |  |  |
| source_id | varchar(32) |  |  |
| country_id | varchar(8) |  |  |
| program_id | varchar(32) |  |  |
| visa_category | varchar(32) |  |  |
| document_type | varchar(64) |  |  |
| url | text |  |  |
| title | varchar(512) | yes |  |
| published_date | varchar(32) | yes |  |
| effective_date | varchar(32) | yes |  |
| retrieved_at | timestamp |  |  |
| content_hash | varchar(64) |  |  |
| current_version | integer |  |  |
| language | varchar(8) |  |  |
| raw_path | text |  |  |
| extracted_path | text | yes |  |
| status | varchar(32) |  |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |

### government_sources

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| source_id | varchar(32) |  |  |
| country_id | varchar(8) |  |  |
| program_id | varchar(32) |  |  |
| visa_category | varchar(32) |  |  |
| authority | varchar(128) |  |  |
| url | text |  |  |
| source_type | varchar(8) |  |  |
| source_subtype | varchar(64) | yes |  |
| priority | varchar(16) |  |  |
| monitoring_method | varchar(16) |  |  |
| crawl_frequency | varchar(16) |  |  |
| last_checked_at | timestamp | yes |  |
| last_hash | varchar(64) | yes |  |
| status | varchar(32) |  |  |
| notes | text | yes |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |
| last_successful_fetch_at | timestamp | yes |  |
| fallback_source_id | varchar(32) | yes |  |
| fetch_status | varchar(32) | yes |  |
| http_status | integer | yes |  |
| last_error | text | yes |  |
| consecutive_failures | integer |  |  |
| last_error_type | varchar(64) | yes |  |
| etag | varchar(256) | yes |  |
| last_modified | varchar(64) | yes |  |
| extract_requirements | boolean |  |  |
| content_kind | varchar(16) |  |  |

### host_cooldowns

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| host | varchar(255) |  |  |
| cooldown_until | timestamp |  |  |
| http_status | integer | yes |  |
| hits | integer |  |  |
| source_id | varchar(32) | yes |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |

### immigration_programs

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| program_id | varchar(32) |  |  |
| country_id | varchar(8) |  |  |
| visa_category | varchar(32) |  |  |
| subclass_code | varchar(8) | yes |  |
| name | varchar(256) |  |  |
| status | varchar(16) |  |  |

### llm_budget_log

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| log_id | uuid |  |  |
| period | varchar(16) |  |  |
| period_start | timestamp |  |  |
| provider | varchar(32) | yes |  |
| total_cost_usd | numeric(16, 6) |  |  |
| total_calls | integer |  |  |
| total_tokens | integer |  |  |
| budget_limit_usd | numeric(16, 6) | yes |  |
| exceeded | boolean |  |  |

### llm_usage

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| usage_id | uuid |  |  |
| request_id | varchar(36) | yes |  |
| user_id | uuid | yes |  |
| provider | varchar(32) |  |  |
| model | varchar(64) |  |  |
| prompt_tokens | integer |  |  |
| completion_tokens | integer |  |  |
| total_tokens | integer |  |  |
| cost_usd | numeric(16, 6) | yes |  |
| latency_ms | integer | yes |  |
| success | boolean |  |  |
| error_message | text | yes |  |
| created_at | timestamp |  |  |

### pipeline_runs

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| run_id | varchar(36) |  |  |
| source_id | varchar(32) |  |  |
| trigger_type | varchar(16) |  |  |
| status | varchar(16) |  |  |
| started_at | timestamp |  |  |
| completed_at | timestamp | yes |  |
| duration_ms | integer | yes |  |
| old_hash | varchar(64) | yes |  |
| new_hash | varchar(64) | yes |  |
| requirements_extracted | integer |  |  |
| requirements_approved | integer |  |  |
| rules_created | integer |  |  |
| rules_validated | integer |  |  |
| rules_approved | integer |  |  |
| rules_activated | integer |  |  |
| errors | json | yes |  |
| metadata_json | json | yes |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |
| stored_new | integer |  |  |

### requirement_evidence

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| evidence_id | integer |  |  |
| requirement_id | varchar(96) |  |  |
| document_id | varchar(32) |  |  |
| document_version | integer |  |  |
| chunk_id | integer |  |  |
| evidence_text | text |  |  |
| evidence_type | varchar(32) |  |  |
| verified | smallint |  |  |
| verification_note | text | yes |  |
| extraction_method | varchar(32) |  |  |
| extracted_at | timestamp |  |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |
| document_version_id | integer | yes |  |

### requirement_references

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| reference_id | integer |  |  |
| requirement_id | varchar(96) |  |  |
| reference_type | varchar(32) |  |  |
| target_text | text |  |  |
| resolved_chunk_id | integer | yes |  |
| status | varchar(32) |  |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |

### requirement_review

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| review_id | integer |  |  |
| requirement_id | varchar(96) |  |  |
| reviewer | varchar(64) |  |  |
| action | varchar(32) |  |  |
| old_state_json | text | yes |  |
| new_state_json | text | yes |  |
| notes | text | yes |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |

### requirement_versions

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| version_id | integer |  |  |
| requirement_id | varchar(96) |  |  |
| version_number | integer |  |  |
| snapshot_json | text |  |  |
| document_version | integer |  |  |
| change_type | varchar(16) |  |  |
| created_by | varchar(64) |  |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |
| document_version_id | integer | yes |  |

### requirements

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| requirement_id | varchar(96) |  |  |
| semantic_key | varchar(256) |  |  |
| program_id | varchar(32) |  |  |
| country_id | varchar(8) |  |  |
| category | varchar(64) |  |  |
| name | varchar(256) |  |  |
| description | text | yes |  |
| field | varchar(128) | yes |  |
| operator | varchar(16) |  |  |
| value_json | text | yes |  |
| unit | varchar(32) | yes |  |
| mandatory | smallint | yes |  |
| condition_json | text | yes |  |
| exception_json | text | yes |  |
| applies_to | text | yes |  |
| content_kind | varchar(32) |  |  |
| confidence | double precision | yes |  |
| criticality | varchar(32) |  |  |
| status | varchar(32) |  |  |
| current_version | integer |  |  |
| extraction_run_id | varchar(24) |  |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |
| document_version_id | integer | yes |  |
| concept | varchar(256) | yes |  |

### rule_versions

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| version_id | integer |  |  |
| rule_id | varchar(96) |  |  |
| version_number | integer |  |  |
| snapshot_json | json |  |  |
| change_type | varchar(16) |  |  |
| created_by | varchar(64) |  |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |
| requirement_version_id | integer | yes |  |

### rules

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| rule_id | varchar(96) |  |  |
| requirement_id | varchar(96) |  |  |
| program_id | varchar(32) |  |  |
| category | varchar(64) |  |  |
| field | varchar(128) |  |  |
| operator | varchar(16) |  |  |
| value_json | text | yes |  |
| unit | varchar(32) | yes |  |
| mandatory | boolean |  |  |
| critical | boolean |  |  |
| message | text |  |  |
| status | varchar(16) |  |  |
| current_version | integer |  |  |
| expression_json | json | yes |  |
| condition_json | json | yes |  |
| exception_json | json | yes |  |
| source_id | varchar(32) | yes |  |
| document_id | varchar(32) | yes |  |
| document_version | integer | yes |  |
| chunk_id | integer | yes |  |
| evidence_text | text | yes |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |
| requirement_version_id | integer | yes |  |

### ruleset_rules

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| ruleset_id | varchar(36) |  |  |
| rule_version_id | integer |  |  |
| position | integer |  |  |

### rulesets

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| ruleset_id | varchar(36) |  |  |
| program_id | varchar(32) |  |  |
| content_hash | varchar(64) |  |  |
| status | varchar(16) |  |  |
| released_at | timestamp |  |  |
| released_by | varchar(64) |  |  |
| rule_count | integer |  |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |

### scheduler_settings

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| id | integer |  |  |
| enabled | boolean |  |  |
| mode | varchar(16) |  |  |
| hour_utc | integer | yes |  |
| minute_utc | integer | yes |  |
| interval_minutes | integer | yes |  |
| updated_by | varchar(256) | yes |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |

### user_sessions

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| session_id | uuid |  |  |
| user_id | uuid |  |  |
| token_hash | varchar(64) |  |  |
| expires_at | timestamp |  |  |
| revoked | boolean |  |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |

### visa_selections

| Column | Type | Nullable | Primary key |
|---|---|---|---|
| selection_id | uuid |  |  |
| applicant_id | uuid |  |  |
| program_id | varchar(32) |  |  |
| selected_at | timestamp |  |  |
| created_at | timestamp |  |  |
| updated_at | timestamp |  |  |
