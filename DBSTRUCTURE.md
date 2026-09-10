# Database Structure Report

Backend: live PostgreSQL (exact counts via `SELECT COUNT(*)`).

## Migration status

- Current alembic revision applied to DB: `f8a9b0c1d2e3` (matches migration-head in `migrations/versions/`).
- Migration files (7 present): 0ceaaa268719_schema_verification.py, 49b9088f3a03_initial_schema_from_models.py, a1b2c3d4e5f6_add_last_successful_fetch_at.py, c7d8e9f0a1b2_add_discovered_sources.py, d1f2e3a4b5c6_add_fallback_fields_to_government_sources.py, f7a6d5c4b3a2_lineage_and_immutability.py, f8a9b0c1d2e3_audit_hardening.py (head)

## Tables (30) — columns / current row counts

| Table | Cols | Rows |
|---|---|---|
| alembic_version | 1 | 1 |
| api_users | 9 | 1 |
| applicant_profiles | 19 | 0 |
| assessment_rule_results | 14 | 0 |
| assessments | 17 | 0 |
| audit_log | 10 | 0 |
| countries | 3 | 1 |
| discovered_sources | 16 | 0 |
| document_chunks | 10 | 8 |
| document_versions | 11 | 9 |
| extraction_runs | 21 | 1 |
| fetch_log | 9 | 17 |
| gaps | 11 | 0 |
| government_documents | 19 | 9 |
| government_sources | 23 | 9 |
| immigration_programs | 6 | 7 |
| llm_budget_log | 9 | 0 |
| llm_usage | 13 | 0 |
| pipeline_runs | 19 | 45 |
| requirement_evidence | 14 | 4 |
| requirement_references | 8 | 0 |
| requirement_review | 9 | 0 |
| requirement_versions | 10 | 4 |
| requirements | 24 | 4 |
| rule_versions | 9 | 0 |
| rules | 24 | 0 |
| ruleset_rules | 3 | 0 |
| rulesets | 9 | 0 |
| user_sessions | 7 | 0 |
| visa_selections | 6 | 0 |

## Column detail

### alembic_version

| column | type | nullable | PK | default |
|---|---|---|---|---|
| version_num | character varying(32) |  | PK |  |

### api_users

| column | type | nullable | PK | default |
|---|---|---|---|---|
| user_id | uuid |  | PK |  |
| email | character varying(256) |  |  |  |
| password_hash | character varying(256) |  |  |  |
| full_name | character varying(256) | yes |  |  |
| role | character varying(32) |  |  |  |
| is_active | boolean |  |  |  |
| last_login_at | timestamp with time zone | yes |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |

### applicant_profiles

| column | type | nullable | PK | default |
|---|---|---|---|---|
| applicant_id | uuid |  | PK |  |
| user_id | uuid |  |  |  |
| first_name | character varying(128) | yes |  |  |
| last_name | character varying(128) | yes |  |  |
| date_of_birth | timestamp with time zone | yes |  |  |
| passport_nationality | character varying(8) | yes |  |  |
| current_visa_subclass | character varying(8) | yes |  |  |
| profile_json | json |  |  |  |
| age | integer | yes |  |  |
| english_level | character varying(32) | yes |  |  |
| points | integer | yes |  |  |
| occupation | character varying(32) | yes |  |  |
| skills_assessment_status | character varying(32) | yes |  |  |
| health_status | character varying(32) | yes |  |  |
| character_status | character varying(32) | yes |  |  |
| has_regional_nomination | boolean | yes |  |  |
| visa_subclass | character varying(8) | yes |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |

### assessment_rule_results

| column | type | nullable | PK | default |
|---|---|---|---|---|
| result_id | uuid |  | PK |  |
| assessment_id | uuid |  |  |  |
| rule_id | character varying(48) |  |  |  |
| rule_version | integer |  |  |  |
| status | character varying(16) |  |  |  |
| field | character varying(128) | yes |  |  |
| actual_value | text | yes |  |  |
| expected_value | text | yes |  |  |
| operator | character varying(16) | yes |  |  |
| message | text | yes |  |  |
| reason | text | yes |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |
| rule_version_id | integer | yes |  |  |

### assessments

| column | type | nullable | PK | default |
|---|---|---|---|---|
| assessment_id | uuid |  | PK |  |
| applicant_id | uuid |  |  |  |
| program_id | character varying(32) |  |  |  |
| business_status | character varying(16) |  |  |  |
| overall_status | character varying(16) |  |  |  |
| total_rules | integer |  |  |  |
| passed_count | integer |  |  |  |
| failed_count | integer |  |  |  |
| unknown_count | integer |  |  |  |
| not_applicable_count | integer |  |  |  |
| evaluated_at | timestamp with time zone |  |  |  |
| request_id | character varying(36) | yes |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |
| applicant_snapshot | json | yes |  |  |
| ruleset_hash | character varying(64) | yes |  |  |
| ruleset_id | character varying(36) | yes |  |  |

### audit_log

| column | type | nullable | PK | default |
|---|---|---|---|---|
| audit_id | uuid |  | PK |  |
| user_id | uuid | yes |  |  |
| action | character varying(64) |  |  |  |
| entity_type | character varying(64) |  |  |  |
| entity_id | character varying(64) | yes |  |  |
| before_json | json | yes |  |  |
| after_json | json | yes |  |  |
| request_id | character varying(36) | yes |  |  |
| ip_address | character varying(64) | yes |  |  |
| created_at | timestamp with time zone |  |  |  |

### countries

| column | type | nullable | PK | default |
|---|---|---|---|---|
| country_id | character varying(8) |  | PK |  |
| name | character varying(128) |  |  |  |
| iso_code | character varying(8) |  |  |  |

### discovered_sources

| column | type | nullable | PK | default |
|---|---|---|---|---|
| discovered_source_id | character varying(32) |  | PK |  |
| seed_url | text |  |  |  |
| candidate_url | text |  |  |  |
| page_title | character varying(512) | yes |  |  |
| matched_keywords | json |  |  |  |
| confidence_score | integer |  |  |  |
| suggested_visa_category | character varying(32) | yes |  |  |
| suggested_country_id | character varying(8) |  |  |  |
| status | character varying(24) |  |  |  |
| discovered_at | timestamp with time zone |  |  |  |
| discovery_run_id | character varying(36) | yes |  |  |
| reviewed_by | character varying(36) | yes |  |  |
| reviewed_at | timestamp with time zone | yes |  |  |
| rejection_reason | text | yes |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |

### document_chunks

| column | type | nullable | PK | default |
|---|---|---|---|---|
| chunk_id | integer |  | PK | `nextval('document_chunks_chunk_id_seq'::regclass)` |
| document_id | character varying(32) |  |  |  |
| version_id | integer |  |  |  |
| chunk_type | character varying(16) |  |  |  |
| heading_path | character varying(512) | yes |  |  |
| order_index | integer |  |  |  |
| raw_text | text |  |  |  |
| page_number | integer | yes |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |

### document_versions

| column | type | nullable | PK | default |
|---|---|---|---|---|
| version_id | integer |  | PK | `nextval('document_versions_version_id_seq'::regclass)` |
| document_id | character varying(32) |  |  |  |
| version_number | integer |  |  |  |
| content_hash | character varying(64) |  |  |  |
| retrieved_at | timestamp with time zone |  |  |  |
| raw_path | text |  |  |  |
| extracted_path | text | yes |  |  |
| change_type | character varying(16) |  |  |  |
| diff_summary | text | yes |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |

### extraction_runs

| column | type | nullable | PK | default |
|---|---|---|---|---|
| run_id | character varying(24) |  | PK |  |
| document_id | character varying(32) |  |  |  |
| document_version | integer |  |  |  |
| model | character varying(64) |  |  |  |
| prompt_version | character varying(64) |  |  |  |
| chunks_seen | integer |  |  |  |
| candidate_chunks | integer |  |  |  |
| llm_calls | integer |  |  |  |
| requirements_extracted | integer |  |  |  |
| validated | integer |  |  |  |
| needs_review | integer |  |  |  |
| rejected | integer |  |  |  |
| evidence_failures | integer |  |  |  |
| schema_failures | integer |  |  |  |
| status | character varying(16) |  |  |  |
| dry_run | smallint |  |  |  |
| error | text | yes |  |  |
| completed_at | timestamp with time zone | yes |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |
| document_version_id | integer | yes |  |  |

### fetch_log

| column | type | nullable | PK | default |
|---|---|---|---|---|
| log_id | integer |  | PK | `nextval('fetch_log_log_id_seq'::regclass)` |
| source_id | character varying(32) |  |  |  |
| fetched_at | timestamp with time zone |  |  |  |
| http_status | integer | yes |  |  |
| status | character varying(32) |  |  |  |
| content_hash | character varying(64) | yes |  |  |
| changed | smallint |  |  |  |
| duration_ms | integer | yes |  |  |
| error | text | yes |  |  |

### gaps

| column | type | nullable | PK | default |
|---|---|---|---|---|
| gap_id | uuid |  | PK |  |
| assessment_id | uuid |  |  |  |
| field | character varying(128) |  |  |  |
| priority | character varying(16) |  |  |  |
| message | text |  |  |  |
| rule_id | character varying(48) | yes |  |  |
| evidence_text | text | yes |  |  |
| answer | text | yes |  |  |
| source_chunk_ids | json | yes |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |

### government_documents

| column | type | nullable | PK | default |
|---|---|---|---|---|
| document_id | character varying(32) |  | PK |  |
| source_id | character varying(32) |  |  |  |
| country_id | character varying(8) |  |  |  |
| program_id | character varying(32) |  |  |  |
| visa_category | character varying(32) |  |  |  |
| document_type | character varying(64) |  |  |  |
| url | text |  |  |  |
| title | character varying(512) | yes |  |  |
| published_date | character varying(32) | yes |  |  |
| effective_date | character varying(32) | yes |  |  |
| retrieved_at | timestamp with time zone |  |  |  |
| content_hash | character varying(64) |  |  |  |
| current_version | integer |  |  |  |
| language | character varying(8) |  |  |  |
| raw_path | text |  |  |  |
| extracted_path | text | yes |  |  |
| status | character varying(32) |  |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |

### government_sources

| column | type | nullable | PK | default |
|---|---|---|---|---|
| source_id | character varying(32) |  | PK |  |
| country_id | character varying(8) |  |  |  |
| program_id | character varying(32) |  |  |  |
| visa_category | character varying(32) |  |  |  |
| authority | character varying(128) |  |  |  |
| url | text |  |  |  |
| source_type | character varying(8) |  |  |  |
| source_subtype | character varying(64) | yes |  |  |
| priority | character varying(16) |  |  |  |
| monitoring_method | character varying(16) |  |  |  |
| crawl_frequency | character varying(16) |  |  |  |
| last_checked_at | timestamp with time zone | yes |  |  |
| last_hash | character varying(64) | yes |  |  |
| status | character varying(32) |  |  |  |
| notes | text | yes |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |
| last_successful_fetch_at | timestamp with time zone | yes |  |  |
| fallback_source_id | character varying(32) | yes |  |  |
| fetch_status | character varying(32) | yes |  |  |
| http_status | integer | yes |  |  |
| last_error | text | yes |  |  |
| consecutive_failures | integer |  |  | `0` |

### immigration_programs

| column | type | nullable | PK | default |
|---|---|---|---|---|
| program_id | character varying(32) |  | PK |  |
| country_id | character varying(8) |  |  |  |
| visa_category | character varying(32) |  |  |  |
| subclass_code | character varying(8) | yes |  |  |
| name | character varying(256) |  |  |  |
| status | character varying(16) |  |  |  |

### llm_budget_log

| column | type | nullable | PK | default |
|---|---|---|---|---|
| log_id | uuid |  | PK |  |
| period | character varying(16) |  |  |  |
| period_start | timestamp with time zone |  |  |  |
| provider | character varying(32) | yes |  |  |
| total_cost_usd | numeric(16,6) |  |  |  |
| total_calls | integer |  |  |  |
| total_tokens | integer |  |  |  |
| budget_limit_usd | numeric(16,6) | yes |  |  |
| exceeded | boolean |  |  |  |

### llm_usage

| column | type | nullable | PK | default |
|---|---|---|---|---|
| usage_id | uuid |  | PK |  |
| request_id | character varying(36) | yes |  |  |
| user_id | uuid | yes |  |  |
| provider | character varying(32) |  |  |  |
| model | character varying(64) |  |  |  |
| prompt_tokens | integer |  |  |  |
| completion_tokens | integer |  |  |  |
| total_tokens | integer |  |  |  |
| cost_usd | numeric(16,6) | yes |  |  |
| latency_ms | integer | yes |  |  |
| success | boolean |  |  |  |
| error_message | text | yes |  |  |
| created_at | timestamp with time zone |  |  |  |

### pipeline_runs

| column | type | nullable | PK | default |
|---|---|---|---|---|
| run_id | character varying(36) |  | PK |  |
| source_id | character varying(32) |  |  |  |
| trigger_type | character varying(16) |  |  |  |
| status | character varying(16) |  |  |  |
| started_at | timestamp with time zone |  |  |  |
| completed_at | timestamp with time zone | yes |  |  |
| duration_ms | integer | yes |  |  |
| old_hash | character varying(64) | yes |  |  |
| new_hash | character varying(64) | yes |  |  |
| requirements_extracted | integer |  |  |  |
| requirements_approved | integer |  |  |  |
| rules_created | integer |  |  |  |
| rules_validated | integer |  |  |  |
| rules_approved | integer |  |  |  |
| rules_activated | integer |  |  |  |
| errors | json | yes |  |  |
| metadata_json | json | yes |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |

### requirement_evidence

| column | type | nullable | PK | default |
|---|---|---|---|---|
| evidence_id | integer |  | PK | `nextval('requirement_evidence_evidence_id_seq'::regclass)` |
| requirement_id | character varying(48) |  |  |  |
| document_id | character varying(32) |  |  |  |
| document_version | integer |  |  |  |
| chunk_id | integer |  |  |  |
| evidence_text | text |  |  |  |
| evidence_type | character varying(32) |  |  |  |
| verified | smallint |  |  |  |
| verification_note | text | yes |  |  |
| extraction_method | character varying(32) |  |  |  |
| extracted_at | timestamp with time zone |  |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |
| document_version_id | integer | yes |  |  |

### requirement_references

| column | type | nullable | PK | default |
|---|---|---|---|---|
| reference_id | integer |  | PK | `nextval('requirement_references_reference_id_seq'::regclass)` |
| requirement_id | character varying(48) |  |  |  |
| reference_type | character varying(32) |  |  |  |
| target_text | text |  |  |  |
| resolved_chunk_id | integer | yes |  |  |
| status | character varying(32) |  |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |

### requirement_review

| column | type | nullable | PK | default |
|---|---|---|---|---|
| review_id | integer |  | PK | `nextval('requirement_review_review_id_seq'::regclass)` |
| requirement_id | character varying(48) |  |  |  |
| reviewer | character varying(64) |  |  |  |
| action | character varying(32) |  |  |  |
| old_state_json | text | yes |  |  |
| new_state_json | text | yes |  |  |
| notes | text | yes |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |

### requirement_versions

| column | type | nullable | PK | default |
|---|---|---|---|---|
| version_id | integer |  | PK | `nextval('requirement_versions_version_id_seq'::regclass)` |
| requirement_id | character varying(48) |  |  |  |
| version_number | integer |  |  |  |
| snapshot_json | text |  |  |  |
| document_version | integer |  |  |  |
| change_type | character varying(16) |  |  |  |
| created_by | character varying(64) |  |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |
| document_version_id | integer | yes |  |  |

### requirements

| column | type | nullable | PK | default |
|---|---|---|---|---|
| requirement_id | character varying(48) |  | PK |  |
| semantic_key | character varying(256) |  |  |  |
| program_id | character varying(32) |  |  |  |
| country_id | character varying(8) |  |  |  |
| category | character varying(64) |  |  |  |
| name | character varying(256) |  |  |  |
| description | text | yes |  |  |
| field | character varying(128) | yes |  |  |
| operator | character varying(16) |  |  |  |
| value_json | text | yes |  |  |
| unit | character varying(32) | yes |  |  |
| mandatory | smallint | yes |  |  |
| condition_json | text | yes |  |  |
| exception_json | text | yes |  |  |
| applies_to | text | yes |  |  |
| content_kind | character varying(32) |  |  |  |
| confidence | double precision | yes |  |  |
| criticality | character varying(32) |  |  |  |
| status | character varying(32) |  |  |  |
| current_version | integer |  |  |  |
| extraction_run_id | character varying(24) |  |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |
| document_version_id | integer | yes |  |  |

### rule_versions

| column | type | nullable | PK | default |
|---|---|---|---|---|
| version_id | integer |  | PK | `nextval('rule_versions_version_id_seq'::regclass)` |
| rule_id | character varying(48) |  |  |  |
| version_number | integer |  |  |  |
| snapshot_json | json |  |  |  |
| change_type | character varying(16) |  |  |  |
| created_by | character varying(64) |  |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |
| requirement_version_id | integer | yes |  |  |

### rules

| column | type | nullable | PK | default |
|---|---|---|---|---|
| rule_id | character varying(48) |  | PK |  |
| requirement_id | character varying(48) |  |  |  |
| program_id | character varying(32) |  |  |  |
| category | character varying(64) |  |  |  |
| field | character varying(128) |  |  |  |
| operator | character varying(16) |  |  |  |
| value_json | text | yes |  |  |
| unit | character varying(32) | yes |  |  |
| mandatory | boolean |  |  |  |
| critical | boolean |  |  |  |
| message | text |  |  |  |
| status | character varying(16) |  |  |  |
| current_version | integer |  |  |  |
| expression_json | json | yes |  |  |
| condition_json | json | yes |  |  |
| exception_json | json | yes |  |  |
| source_id | character varying(32) | yes |  |  |
| document_id | character varying(32) | yes |  |  |
| document_version | integer | yes |  |  |
| chunk_id | integer | yes |  |  |
| evidence_text | text | yes |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |
| requirement_version_id | integer | yes |  |  |

### ruleset_rules

| column | type | nullable | PK | default |
|---|---|---|---|---|
| ruleset_id | character varying(36) |  | PK |  |
| rule_version_id | integer |  | PK |  |
| position | integer |  |  |  |

### rulesets

| column | type | nullable | PK | default |
|---|---|---|---|---|
| ruleset_id | character varying(36) |  | PK |  |
| program_id | character varying(32) |  |  |  |
| content_hash | character varying(64) |  |  |  |
| status | character varying(16) |  |  |  |
| released_at | timestamp with time zone |  |  |  |
| released_by | character varying(64) |  |  |  |
| rule_count | integer |  |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |

### user_sessions

| column | type | nullable | PK | default |
|---|---|---|---|---|
| session_id | uuid |  | PK |  |
| user_id | uuid |  |  |  |
| token_hash | character varying(64) |  |  |  |
| expires_at | timestamp with time zone |  |  |  |
| revoked | boolean |  |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |

### visa_selections

| column | type | nullable | PK | default |
|---|---|---|---|---|
| selection_id | uuid |  | PK |  |
| applicant_id | uuid |  |  |  |
| program_id | character varying(32) |  |  |  |
| selected_at | timestamp with time zone |  |  |  |
| created_at | timestamp with time zone |  |  |  |
| updated_at | timestamp with time zone |  |  |  |

## Primary keys

- alembic_version.version_num
- api_users.user_id
- applicant_profiles.applicant_id
- assessment_rule_results.result_id
- assessments.assessment_id
- audit_log.audit_id
- countries.country_id
- discovered_sources.discovered_source_id
- document_chunks.chunk_id
- document_versions.version_id
- extraction_runs.run_id
- fetch_log.log_id
- gaps.gap_id
- government_documents.document_id
- government_sources.source_id
- immigration_programs.program_id
- llm_budget_log.log_id
- llm_usage.usage_id
- pipeline_runs.run_id
- requirement_evidence.evidence_id
- requirement_references.reference_id
- requirement_review.review_id
- requirement_versions.version_id
- requirements.requirement_id
- rule_versions.version_id
- rules.rule_id
- ruleset_rules.ruleset_id, rule_version_id
- rulesets.ruleset_id
- user_sessions.session_id
- visa_selections.selection_id

## Foreign keys

- applicant_profiles_user_id_fkey (applicant_profiles): FOREIGN KEY (user_id) REFERENCES api_users(user_id)
- assessment_rule_results_assessment_id_fkey (assessment_rule_results): FOREIGN KEY (assessment_id) REFERENCES assessments(assessment_id)
- assessment_rule_results_rule_id_fkey (assessment_rule_results): FOREIGN KEY (rule_id) REFERENCES rules(rule_id)
- fk_result_rule_version (assessment_rule_results): FOREIGN KEY (rule_version_id) REFERENCES rule_versions(version_id)
- assessments_applicant_id_fkey (assessments): FOREIGN KEY (applicant_id) REFERENCES applicant_profiles(applicant_id)
- assessments_program_id_fkey (assessments): FOREIGN KEY (program_id) REFERENCES immigration_programs(program_id)
- fk_assessment_ruleset (assessments): FOREIGN KEY (ruleset_id) REFERENCES rulesets(ruleset_id)
- audit_log_user_id_fkey (audit_log): FOREIGN KEY (user_id) REFERENCES api_users(user_id)
- fk_discovered_suggested_country (discovered_sources): FOREIGN KEY (suggested_country_id) REFERENCES countries(country_id)
- document_chunks_document_id_fkey (document_chunks): FOREIGN KEY (document_id) REFERENCES government_documents(document_id)
- document_chunks_version_id_fkey (document_chunks): FOREIGN KEY (version_id) REFERENCES document_versions(version_id)
- fk_chunks_doc_version_pair (document_chunks): FOREIGN KEY (document_id, version_id) REFERENCES document_versions(document_id, version_id)
- document_versions_document_id_fkey (document_versions): FOREIGN KEY (document_id) REFERENCES government_documents(document_id)
- extraction_runs_document_id_fkey (extraction_runs): FOREIGN KEY (document_id) REFERENCES government_documents(document_id)
- fk_extract_run_doc_version_pair (extraction_runs): FOREIGN KEY (document_id, document_version) REFERENCES document_versions(document_id, version_number)
- fk_extract_run_version (extraction_runs): FOREIGN KEY (document_version_id) REFERENCES document_versions(version_id)
- fetch_log_source_id_fkey (fetch_log): FOREIGN KEY (source_id) REFERENCES government_sources(source_id)
- gaps_assessment_id_fkey (gaps): FOREIGN KEY (assessment_id) REFERENCES assessments(assessment_id)
- fk_doc_program_country (government_documents): FOREIGN KEY (program_id, country_id) REFERENCES immigration_programs(program_id, country_id)
- government_documents_country_id_fkey (government_documents): FOREIGN KEY (country_id) REFERENCES countries(country_id)
- government_documents_program_id_fkey (government_documents): FOREIGN KEY (program_id) REFERENCES immigration_programs(program_id)
- government_documents_source_id_fkey (government_documents): FOREIGN KEY (source_id) REFERENCES government_sources(source_id)
- fk_government_sources_fallback (government_sources): FOREIGN KEY (fallback_source_id) REFERENCES government_sources(source_id)
- fk_src_program_country (government_sources): FOREIGN KEY (program_id, country_id) REFERENCES immigration_programs(program_id, country_id)
- government_sources_country_id_fkey (government_sources): FOREIGN KEY (country_id) REFERENCES countries(country_id)
- government_sources_program_id_fkey (government_sources): FOREIGN KEY (program_id) REFERENCES immigration_programs(program_id)
- immigration_programs_country_id_fkey (immigration_programs): FOREIGN KEY (country_id) REFERENCES countries(country_id)
- llm_usage_user_id_fkey (llm_usage): FOREIGN KEY (user_id) REFERENCES api_users(user_id)
- pipeline_runs_source_id_fkey (pipeline_runs): FOREIGN KEY (source_id) REFERENCES government_sources(source_id)
- fk_evidence_chunk_lineage (requirement_evidence): FOREIGN KEY (chunk_id, document_id, document_version_id) REFERENCES document_chunks(chunk_id, document_id, version_id)
- fk_evidence_doc_version_pair (requirement_evidence): FOREIGN KEY (document_id, document_version) REFERENCES document_versions(document_id, version_number)
- fk_evidence_version (requirement_evidence): FOREIGN KEY (document_version_id) REFERENCES document_versions(version_id)
- requirement_evidence_chunk_id_fkey (requirement_evidence): FOREIGN KEY (chunk_id) REFERENCES document_chunks(chunk_id)
- requirement_evidence_document_id_fkey (requirement_evidence): FOREIGN KEY (document_id) REFERENCES government_documents(document_id)
- requirement_evidence_requirement_id_fkey (requirement_evidence): FOREIGN KEY (requirement_id) REFERENCES requirements(requirement_id)
- requirement_references_requirement_id_fkey (requirement_references): FOREIGN KEY (requirement_id) REFERENCES requirements(requirement_id)
- requirement_references_resolved_chunk_id_fkey (requirement_references): FOREIGN KEY (resolved_chunk_id) REFERENCES document_chunks(chunk_id)
- requirement_review_requirement_id_fkey (requirement_review): FOREIGN KEY (requirement_id) REFERENCES requirements(requirement_id)
- fk_reqver_version (requirement_versions): FOREIGN KEY (document_version_id) REFERENCES document_versions(version_id)
- requirement_versions_requirement_id_fkey (requirement_versions): FOREIGN KEY (requirement_id) REFERENCES requirements(requirement_id)
- fk_req_program_country (requirements): FOREIGN KEY (program_id, country_id) REFERENCES immigration_programs(program_id, country_id)
- fk_req_version (requirements): FOREIGN KEY (document_version_id) REFERENCES document_versions(version_id)
- requirements_country_id_fkey (requirements): FOREIGN KEY (country_id) REFERENCES countries(country_id)
- requirements_extraction_run_id_fkey (requirements): FOREIGN KEY (extraction_run_id) REFERENCES extraction_runs(run_id)
- requirements_program_id_fkey (requirements): FOREIGN KEY (program_id) REFERENCES immigration_programs(program_id)
- fk_rulever_req_version (rule_versions): FOREIGN KEY (requirement_version_id) REFERENCES requirement_versions(version_id)
- rule_versions_rule_id_fkey (rule_versions): FOREIGN KEY (rule_id) REFERENCES rules(rule_id)
- fk_rule_req_program_pair (rules): FOREIGN KEY (requirement_id, program_id) REFERENCES requirements(requirement_id, program_id)
- fk_rule_req_version (rules): FOREIGN KEY (requirement_version_id) REFERENCES requirement_versions(version_id)
- rules_program_id_fkey (rules): FOREIGN KEY (program_id) REFERENCES immigration_programs(program_id)
- rules_requirement_id_fkey (rules): FOREIGN KEY (requirement_id) REFERENCES requirements(requirement_id)
- ruleset_rules_rule_version_id_fkey (ruleset_rules): FOREIGN KEY (rule_version_id) REFERENCES rule_versions(version_id)
- ruleset_rules_ruleset_id_fkey (ruleset_rules): FOREIGN KEY (ruleset_id) REFERENCES rulesets(ruleset_id)
- rulesets_program_id_fkey (rulesets): FOREIGN KEY (program_id) REFERENCES immigration_programs(program_id)
- user_sessions_user_id_fkey (user_sessions): FOREIGN KEY (user_id) REFERENCES api_users(user_id)
- visa_selections_applicant_id_fkey (visa_selections): FOREIGN KEY (applicant_id) REFERENCES applicant_profiles(applicant_id)
- visa_selections_program_id_fkey (visa_selections): FOREIGN KEY (program_id) REFERENCES immigration_programs(program_id)

## Unique indexes / constraints

- api_users_email_key (api_users): CREATE UNIQUE INDEX api_users_email_key ON public.api_users USING btree (email)
- discovered_sources_candidate_url_key (discovered_sources): CREATE UNIQUE INDEX discovered_sources_candidate_url_key ON public.discovered_sources USING btree (candidate_url)
- government_sources_url_key (government_sources): CREATE UNIQUE INDEX government_sources_url_key ON public.government_sources USING btree (url)
- uq_applicant_program (visa_selections): CREATE UNIQUE INDEX uq_applicant_program ON public.visa_selections USING btree (applicant_id, program_id)
- uq_chunk_lineage (document_chunks): CREATE UNIQUE INDEX uq_chunk_lineage ON public.document_chunks USING btree (chunk_id, document_id, version_id)
- uq_chunk_version_order (document_chunks): CREATE UNIQUE INDEX uq_chunk_version_order ON public.document_chunks USING btree (version_id, order_index)
- uq_doc_ver_content (document_versions): CREATE UNIQUE INDEX uq_doc_ver_content ON public.document_versions USING btree (document_id, content_hash)
- uq_doc_ver_docid_versionid (document_versions): CREATE UNIQUE INDEX uq_doc_ver_docid_versionid ON public.document_versions USING btree (document_id, version_id)
- uq_doc_version (document_versions): CREATE UNIQUE INDEX uq_doc_version ON public.document_versions USING btree (document_id, version_number)
- uq_gov_src_program_country (immigration_programs): CREATE UNIQUE INDEX uq_gov_src_program_country ON public.immigration_programs USING btree (program_id, country_id)
- uq_req_active_semantic (requirements): CREATE UNIQUE INDEX uq_req_active_semantic ON public.requirements USING btree (program_id, semantic_key) WHERE ((status)::text <> ALL ((ARRAY['CONFLICT'::character varying, 'REJECTED'::character varying])::text[]))
- uq_req_program_pair (requirements): CREATE UNIQUE INDEX uq_req_program_pair ON public.requirements USING btree (requirement_id, program_id)
- uq_req_version (requirement_versions): CREATE UNIQUE INDEX uq_req_version ON public.requirement_versions USING btree (requirement_id, version_number)
- uq_result_assessment_rule (assessment_rule_results): CREATE UNIQUE INDEX uq_result_assessment_rule ON public.assessment_rule_results USING btree (assessment_id, rule_id)
- uq_rule_version (rule_versions): CREATE UNIQUE INDEX uq_rule_version ON public.rule_versions USING btree (rule_id, version_number)
- uq_rules_active_per_field (rules): CREATE UNIQUE INDEX uq_rules_active_per_field ON public.rules USING btree (program_id, field) WHERE ((status)::text = 'ACTIVE'::text)
- uq_ruleset_content (rulesets): CREATE UNIQUE INDEX uq_ruleset_content ON public.rulesets USING btree (program_id, content_hash)
- uq_ruleset_rule_position (ruleset_rules): CREATE UNIQUE INDEX uq_ruleset_rule_position ON public.ruleset_rules USING btree (ruleset_id, "position")

## Check constraints

- ck_result_status (assessment_rule_results): CHECK (((status)::text = ANY ((ARRAY['PASS'::character varying, 'FAIL'::character varying, 'UNKNOWN'::character varying, 'NOT_APPLICABLE'::character varying, 'ERROR'::character varying])::text[])))
- ck_assessment_business (assessments): CHECK (((business_status)::text = ANY ((ARRAY['ELIGIBLE'::character varying, 'INELIGIBLE'::character varying, 'INCOMPLETE'::character varying])::text[])))
- ck_assessment_overall (assessments): CHECK (((overall_status)::text = ANY ((ARRAY['PASS'::character varying, 'FAIL'::character varying, 'UNKNOWN'::character varying, 'NOT_APPLICABLE'::character varying, 'ERROR'::character varying])::text[])))
- ck_discovered_status (discovered_sources): CHECK (((status)::text = ANY ((ARRAY['PENDING_REVIEW'::character varying, 'APPROVED'::character varying, 'REJECTED'::character varying])::text[])))
- ck_chunk_order_nonneg (document_chunks): CHECK ((order_index >= 0))
- ck_doc_change_type (document_versions): CHECK (((change_type)::text = ANY ((ARRAY['INITIAL'::character varying, 'CHANGED'::character varying, 'UNCHANGED'::character varying, 'MANUAL_CAPTURE'::character varying])::text[])))
- ck_doc_version_positive (document_versions): CHECK ((version_number > 0))
- ck_extract_dry_run (extraction_runs): CHECK ((dry_run = ANY (ARRAY[0, 1])))
- ck_extract_status (extraction_runs): CHECK (((status)::text = ANY ((ARRAY['RUNNING'::character varying, 'COMPLETED'::character varying, 'FAILED'::character varying, 'DRY_RUN'::character varying])::text[])))
- ck_extract_version_positive (extraction_runs): CHECK ((document_version > 0))
- ck_fetch_changed (fetch_log): CHECK ((changed = ANY (ARRAY[0, 1])))
- ck_source_fetch_status (government_sources): CHECK (((fetch_status IS NULL) OR ((fetch_status)::text = ANY ((ARRAY['OK'::character varying, 'FETCH_FAILED'::character varying, 'ACCESS_BLOCKED'::character varying, 'NETWORK_RESTRICTED'::character varying, 'REQUIRES_BROWSER'::character varying, 'REQUIRES_MANUAL_DOWNLOAD'::character varying, 'INVALID_CONTENT'::character varying])::text[]))))
- ck_source_status (government_sources): CHECK (((status)::text = ANY ((ARRAY['ACTIVE'::character varying, 'DISABLED'::character varying, 'ACCESS_BLOCKED'::character varying, 'NETWORK_RESTRICTED'::character varying, 'REQUIRES_BROWSER'::character varying, 'REQUIRES_MANUAL_DOWNLOAD'::character varying, 'NEEDS_MANUAL_CAPTURE'::character varying, 'BROKEN'::character varying])::text[])))
- ck_pipeline_status (pipeline_runs): CHECK (((status)::text = ANY ((ARRAY['RUNNING'::character varying, 'COMPLETED'::character varying, 'FAILED'::character varying, 'PARTIAL'::character varying])::text[])))
- ck_evidence_verified (requirement_evidence): CHECK ((verified = ANY (ARRAY[0, 1])))
- ck_evidence_version_positive (requirement_evidence): CHECK ((document_version > 0))
- ck_reqver_docver (requirement_versions): CHECK ((document_version >= 0))
- ck_reqver_version_number (requirement_versions): CHECK ((version_number > 0))
- ck_req_current_version (requirements): CHECK ((current_version > 0))
- ck_req_mandatory (requirements): CHECK ((mandatory = ANY (ARRAY[0, 1])))
- ck_req_status (requirements): CHECK (((status)::text = ANY ((ARRAY['DRAFT'::character varying, 'VALIDATED'::character varying, 'NEEDS_REVIEW'::character varying, 'APPROVED'::character varying, 'REJECTED'::character varying, 'SUPERSEDED'::character varying, 'CONFLICT'::character varying])::text[])))
- ck_rulever_version_number (rule_versions): CHECK ((version_number > 0))
- ck_rule_current_version (rules): CHECK ((current_version > 0))
- ck_rule_status (rules): CHECK (((status)::text = ANY ((ARRAY['DRAFT'::character varying, 'VALIDATED'::character varying, 'APPROVED'::character varying, 'ACTIVE'::character varying, 'SUPERSEDED'::character varying, 'RETIRED'::character varying, 'REJECTED'::character varying])::text[])))
- ck_ruleset_rule_position (ruleset_rules): CHECK (("position" >= 0))
- ck_ruleset_status (rulesets): CHECK (((status)::text = ANY ((ARRAY['DRAFT'::character varying, 'RELEASED'::character varying])::text[])))

## Other indexes

- idx_user_email: CREATE INDEX idx_user_email ON public.api_users USING btree (email)
- idx_applicant_user: CREATE INDEX idx_applicant_user ON public.applicant_profiles USING btree (user_id)
- idx_assessment_result_assessment: CREATE INDEX idx_assessment_result_assessment ON public.assessment_rule_results USING btree (assessment_id)
- idx_assessment_result_rule: CREATE INDEX idx_assessment_result_rule ON public.assessment_rule_results USING btree (rule_id)
- idx_result_rulever: CREATE INDEX idx_result_rulever ON public.assessment_rule_results USING btree (rule_version_id)
- idx_assessment_applicant: CREATE INDEX idx_assessment_applicant ON public.assessments USING btree (applicant_id)
- idx_assessment_date: CREATE INDEX idx_assessment_date ON public.assessments USING btree (evaluated_at)
- idx_assessment_program: CREATE INDEX idx_assessment_program ON public.assessments USING btree (program_id)
- idx_assessment_ruleset: CREATE INDEX idx_assessment_ruleset ON public.assessments USING btree (ruleset_id)
- idx_audit_created: CREATE INDEX idx_audit_created ON public.audit_log USING btree (created_at)
- idx_audit_entity: CREATE INDEX idx_audit_entity ON public.audit_log USING btree (entity_type, entity_id)
- idx_audit_user: CREATE INDEX idx_audit_user ON public.audit_log USING btree (user_id)
- idx_discovered_status: CREATE INDEX idx_discovered_status ON public.discovered_sources USING btree (status)
- idx_discovered_category: CREATE INDEX idx_discovered_category ON public.discovered_sources USING btree (suggested_visa_category)
- idx_discovered_run: CREATE INDEX idx_discovered_run ON public.discovered_sources USING btree (discovery_run_id)
- idx_chunks_document: CREATE INDEX idx_chunks_document ON public.document_chunks USING btree (document_id, version_id)
- idx_extraction_runs_document: CREATE INDEX idx_extraction_runs_document ON public.extraction_runs USING btree (document_id, document_version)
- idx_extract_run_dv: CREATE INDEX idx_extract_run_dv ON public.extraction_runs USING btree (document_version_id)
- idx_fetchlog_source: CREATE INDEX idx_fetchlog_source ON public.fetch_log USING btree (source_id, fetched_at)
- idx_gap_assessment: CREATE INDEX idx_gap_assessment ON public.gaps USING btree (assessment_id)
- idx_documents_source: CREATE INDEX idx_documents_source ON public.government_documents USING btree (source_id)
- idx_sources_program: CREATE INDEX idx_sources_program ON public.government_sources USING btree (program_id)
- idx_budget_period: CREATE INDEX idx_budget_period ON public.llm_budget_log USING btree (period, period_start)
- idx_llm_usage_provider: CREATE INDEX idx_llm_usage_provider ON public.llm_usage USING btree (provider, created_at)
- idx_llm_usage_user: CREATE INDEX idx_llm_usage_user ON public.llm_usage USING btree (user_id)
- idx_pipeline_run_source: CREATE INDEX idx_pipeline_run_source ON public.pipeline_runs USING btree (source_id)
- idx_pipeline_run_started: CREATE INDEX idx_pipeline_run_started ON public.pipeline_runs USING btree (started_at)
- idx_pipeline_run_status: CREATE INDEX idx_pipeline_run_status ON public.pipeline_runs USING btree (status)
- idx_evidence_chunk: CREATE INDEX idx_evidence_chunk ON public.requirement_evidence USING btree (chunk_id)
- idx_evidence_requirement: CREATE INDEX idx_evidence_requirement ON public.requirement_evidence USING btree (requirement_id)
- idx_evidence_dv: CREATE INDEX idx_evidence_dv ON public.requirement_evidence USING btree (document_version_id)
- idx_evidence_doc: CREATE INDEX idx_evidence_doc ON public.requirement_evidence USING btree (document_id)
- idx_references_requirement: CREATE INDEX idx_references_requirement ON public.requirement_references USING btree (requirement_id)
- idx_review_requirement: CREATE INDEX idx_review_requirement ON public.requirement_review USING btree (requirement_id)
- idx_req_versions_requirement: CREATE INDEX idx_req_versions_requirement ON public.requirement_versions USING btree (requirement_id)
- idx_reqver_dv: CREATE INDEX idx_reqver_dv ON public.requirement_versions USING btree (document_version_id)
- idx_requirements_category: CREATE INDEX idx_requirements_category ON public.requirements USING btree (category)
- idx_requirements_program: CREATE INDEX idx_requirements_program ON public.requirements USING btree (program_id)
- idx_requirements_semantic_key: CREATE INDEX idx_requirements_semantic_key ON public.requirements USING btree (semantic_key)
- idx_requirements_status: CREATE INDEX idx_requirements_status ON public.requirements USING btree (status)
- idx_req_dv: CREATE INDEX idx_req_dv ON public.requirements USING btree (document_version_id)
- idx_rule_versions_rule: CREATE INDEX idx_rule_versions_rule ON public.rule_versions USING btree (rule_id)
- idx_rulever_reqver: CREATE INDEX idx_rulever_reqver ON public.rule_versions USING btree (requirement_version_id)
- idx_rules_program: CREATE INDEX idx_rules_program ON public.rules USING btree (program_id)
- idx_rules_requirement: CREATE INDEX idx_rules_requirement ON public.rules USING btree (requirement_id)
- idx_rules_status: CREATE INDEX idx_rules_status ON public.rules USING btree (status)
- idx_rule_reqver: CREATE INDEX idx_rule_reqver ON public.rules USING btree (requirement_version_id)
- idx_ruleset_rule_version: CREATE INDEX idx_ruleset_rule_version ON public.ruleset_rules USING btree (rule_version_id)
- idx_session_token: CREATE INDEX idx_session_token ON public.user_sessions USING btree (token_hash)
- idx_session_user: CREATE INDEX idx_session_user ON public.user_sessions USING btree (user_id)
- idx_visa_selection_applicant: CREATE INDEX idx_visa_selection_applicant ON public.visa_selections USING btree (applicant_id)

## Triggers

- assessment_rule_results.trg_assessment_rule_results_append_only
- assessments.trg_assessments_append_only
- document_versions.trg_document_versions_append_only
- government_sources.trg_government_sources_no_self_fallback
- requirement_versions.trg_requirement_versions_append_only
- rule_versions.trg_rule_versions_append_only
- ruleset_rules.trg_ruleset_rules_append_only
- rulesets.trg_rulesets_append_only

## How to check this yourself

```bash
# tables / structure:
docker exec -it immigration-knowledge-postgres-1 psql -U immigration -d immigration -c "\dt"
docker exec -it immigration-knowledge-postgres-1 psql -U immigration -d immigration -c "\d government_sources"
# full DDL for all tables + indexes:
docker exec -it immigration-knowledge-postgres-1 pg_dump -U immigration -d immigration --schema-only
# migration status:
alembic current    # where the DB is
alembic heads      # where migration files go
```