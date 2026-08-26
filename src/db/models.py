"""SQLAlchemy ORM models for the immigration knowledge platform.

Tables cover:
  Phase 1: countries, immigration_programs, government_sources, government_documents,
           document_versions, document_chunks, fetch_log
  Phase 2: extraction_runs, requirements, requirement_evidence, requirement_versions,
           requirement_review, requirement_references
  Phase 3: rules, rule_versions, assessments, assessment_rule_results
  Phase 3C: applicant_profiles, visa_selections, gaps
  Production: api_users, user_sessions, audit_log, llm_usage, llm_budget_log
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey, Index, Integer,
    SmallInteger, String, Text, UniqueConstraint,
    func,
)
from sqlalchemy.types import JSON
from sqlalchemy.orm import DeclarativeBase, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _short_uuid() -> str:
    return uuid.uuid4().hex[:12]


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Helper mixin: every table gets created_at + updated_at
# ---------------------------------------------------------------------------
class TimestampMixin:
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


# ===========================================================================
#  PHASE 1 — Government Source Registry + Document Metadata
# ===========================================================================

class Country(Base):
    __tablename__ = "countries"
    country_id = Column(String(8), primary_key=True)
    name = Column(String(128), nullable=False)
    iso_code = Column(String(8), nullable=False)


class ImmigrationProgram(Base):
    __tablename__ = "immigration_programs"
    program_id = Column(String(32), primary_key=True)
    country_id = Column(String(8), ForeignKey("countries.country_id"), nullable=False)
    visa_category = Column(String(32), nullable=False)
    subclass_code = Column(String(8))
    name = Column(String(256), nullable=False)
    status = Column(String(16), nullable=False, default="ACTIVE")


class GovernmentSource(Base, TimestampMixin):
    __tablename__ = "government_sources"
    source_id = Column(String(32), primary_key=True)
    country_id = Column(String(8), ForeignKey("countries.country_id"), nullable=False)
    program_id = Column(String(32), ForeignKey("immigration_programs.program_id"), nullable=False)
    visa_category = Column(String(32), nullable=False)
    authority = Column(String(128), nullable=False)
    url = Column(Text, nullable=False, unique=True)
    source_type = Column(String(8), nullable=False)
    source_subtype = Column(String(64))
    priority = Column(String(16), nullable=False)
    monitoring_method = Column(String(16), nullable=False, default="WEB")
    crawl_frequency = Column(String(16), nullable=False, default="DAILY")
    last_checked_at = Column(DateTime(timezone=True))
    last_hash = Column(String(64))
    status = Column(String(32), nullable=False, default="ACTIVE")
    notes = Column(Text)

    __table_args__ = (Index("idx_sources_program", "program_id"),)


class GovernmentDocument(Base, TimestampMixin):
    __tablename__ = "government_documents"
    document_id = Column(String(32), primary_key=True)
    source_id = Column(String(32), ForeignKey("government_sources.source_id"), nullable=False)
    country_id = Column(String(8), ForeignKey("countries.country_id"), nullable=False)
    program_id = Column(String(32), ForeignKey("immigration_programs.program_id"), nullable=False)
    visa_category = Column(String(32), nullable=False)
    document_type = Column(String(64), nullable=False)
    url = Column(Text, nullable=False)
    title = Column(String(512))
    published_date = Column(String(32))
    effective_date = Column(String(32))
    retrieved_at = Column(DateTime(timezone=True), nullable=False)
    content_hash = Column(String(64), nullable=False)
    current_version = Column(Integer, nullable=False, default=1)
    language = Column(String(8), nullable=False, default="en")
    raw_path = Column(Text, nullable=False)
    extracted_path = Column(Text)
    status = Column(String(32), nullable=False, default="CURRENT")

    __table_args__ = (Index("idx_documents_source", "source_id"),)


class DocumentVersion(Base, TimestampMixin):
    __tablename__ = "document_versions"
    version_id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(String(32), ForeignKey("government_documents.document_id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    content_hash = Column(String(64), nullable=False)
    retrieved_at = Column(DateTime(timezone=True), nullable=False)
    raw_path = Column(Text, nullable=False)
    extracted_path = Column(Text)
    change_type = Column(String(16), nullable=False)
    diff_summary = Column(Text)

    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_doc_version"),
    )


class DocumentChunk(Base, TimestampMixin):
    __tablename__ = "document_chunks"
    chunk_id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(String(32), ForeignKey("government_documents.document_id"), nullable=False)
    version_id = Column(Integer, ForeignKey("document_versions.version_id"), nullable=False)
    chunk_type = Column(String(16), nullable=False)
    heading_path = Column(String(512))
    order_index = Column(Integer, nullable=False)
    raw_text = Column(Text, nullable=False)
    page_number = Column(Integer)

    __table_args__ = (Index("idx_chunks_document", "document_id", "version_id"),)


class FetchLog(Base):
    __tablename__ = "fetch_log"
    log_id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String(32), ForeignKey("government_sources.source_id"), nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False)
    http_status = Column(Integer)
    status = Column(String(32), nullable=False, default="OK")
    content_hash = Column(String(64))
    changed = Column(SmallInteger, nullable=False, default=0)
    duration_ms = Column(Integer)
    error = Column(Text)

    __table_args__ = (Index("idx_fetchlog_source", "source_id", "fetched_at"),)


# ===========================================================================
#  PHASE 2 — Requirement Knowledge Model
# ===========================================================================

class ExtractionRun(Base, TimestampMixin):
    __tablename__ = "extraction_runs"
    run_id = Column(String(24), primary_key=True)
    document_id = Column(String(32), ForeignKey("government_documents.document_id"), nullable=False)
    document_version = Column(Integer, nullable=False)
    model = Column(String(64), nullable=False)
    prompt_version = Column(String(64), nullable=False)
    chunks_seen = Column(Integer, nullable=False, default=0)
    candidate_chunks = Column(Integer, nullable=False, default=0)
    llm_calls = Column(Integer, nullable=False, default=0)
    requirements_extracted = Column(Integer, nullable=False, default=0)
    validated = Column(Integer, nullable=False, default=0)
    needs_review = Column(Integer, nullable=False, default=0)
    rejected = Column(Integer, nullable=False, default=0)
    evidence_failures = Column(Integer, nullable=False, default=0)
    schema_failures = Column(Integer, nullable=False, default=0)
    status = Column(String(16), nullable=False, default="RUNNING")
    dry_run = Column(SmallInteger, nullable=False, default=0)
    error = Column(Text)
    completed_at = Column(DateTime(timezone=True))

    __table_args__ = (Index("idx_extraction_runs_document", "document_id", "document_version"),)


class Requirement(Base, TimestampMixin):
    __tablename__ = "requirements"
    requirement_id = Column(String(48), primary_key=True)
    semantic_key = Column(String(256), nullable=False)
    program_id = Column(String(32), ForeignKey("immigration_programs.program_id"), nullable=False)
    country_id = Column(String(8), ForeignKey("countries.country_id"), nullable=False)
    category = Column(String(64), nullable=False)
    name = Column(String(256), nullable=False)
    description = Column(Text)
    field = Column(String(128))
    operator = Column(String(16), nullable=False, default="UNKNOWN")
    value_json = Column(Text)
    unit = Column(String(32))
    mandatory = Column(SmallInteger)
    condition_json = Column(Text)
    exception_json = Column(Text)
    applies_to = Column(Text)
    content_kind = Column(String(32), nullable=False, default="REQUIREMENT")
    confidence = Column(Float)
    criticality = Column(String(32), nullable=False, default="NORMAL")
    status = Column(String(32), nullable=False, default="DRAFT")
    current_version = Column(Integer, nullable=False, default=1)
    extraction_run_id = Column(String(24), ForeignKey("extraction_runs.run_id"), nullable=False)

    __table_args__ = (
        Index("idx_requirements_program", "program_id"),
        Index("idx_requirements_semantic_key", "semantic_key"),
        Index("idx_requirements_status", "status"),
        Index("idx_requirements_category", "category"),
    )


class RequirementEvidence(Base, TimestampMixin):
    __tablename__ = "requirement_evidence"
    evidence_id = Column(Integer, primary_key=True, autoincrement=True)
    requirement_id = Column(String(48), ForeignKey("requirements.requirement_id"), nullable=False)
    document_id = Column(String(32), ForeignKey("government_documents.document_id"), nullable=False)
    document_version = Column(Integer, nullable=False)
    chunk_id = Column(Integer, ForeignKey("document_chunks.chunk_id"), nullable=False)
    evidence_text = Column(Text, nullable=False)
    evidence_type = Column(String(32), nullable=False)
    verified = Column(SmallInteger, nullable=False, default=0)
    verification_note = Column(Text)
    extraction_method = Column(String(32), nullable=False, default="LLM_EXTRACTION")
    extracted_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_evidence_requirement", "requirement_id"),
        Index("idx_evidence_chunk", "chunk_id"),
    )


class RequirementVersion(Base, TimestampMixin):
    __tablename__ = "requirement_versions"
    version_id = Column(Integer, primary_key=True, autoincrement=True)
    requirement_id = Column(String(48), ForeignKey("requirements.requirement_id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    snapshot_json = Column(Text, nullable=False)
    document_version = Column(Integer, nullable=False)
    change_type = Column(String(16), nullable=False)
    created_by = Column(String(64), nullable=False, default="system")

    __table_args__ = (
        UniqueConstraint("requirement_id", "version_number", name="uq_req_version"),
        Index("idx_req_versions_requirement", "requirement_id"),
    )


class RequirementReview(Base, TimestampMixin):
    __tablename__ = "requirement_review"
    review_id = Column(Integer, primary_key=True, autoincrement=True)
    requirement_id = Column(String(48), ForeignKey("requirements.requirement_id"), nullable=False)
    reviewer = Column(String(64), nullable=False)
    action = Column(String(32), nullable=False)
    old_state_json = Column(Text)
    new_state_json = Column(Text)
    notes = Column(Text)

    __table_args__ = (Index("idx_review_requirement", "requirement_id"),)


class RequirementReference(Base, TimestampMixin):
    __tablename__ = "requirement_references"
    reference_id = Column(Integer, primary_key=True, autoincrement=True)
    requirement_id = Column(String(48), ForeignKey("requirements.requirement_id"), nullable=False)
    reference_type = Column(String(32), nullable=False, default="LEGAL_CROSS_REFERENCE")
    target_text = Column(Text, nullable=False)
    resolved_chunk_id = Column(Integer, ForeignKey("document_chunks.chunk_id"))
    status = Column(String(32), nullable=False, default="UNRESOLVED")

    __table_args__ = (Index("idx_references_requirement", "requirement_id"),)


# ===========================================================================
#  PHASE 3 — Deterministic Rule Engine
# ===========================================================================

class Rule(Base, TimestampMixin):
    __tablename__ = "rules"
    rule_id = Column(String(48), primary_key=True)
    requirement_id = Column(String(48), ForeignKey("requirements.requirement_id"), nullable=False)
    program_id = Column(String(32), ForeignKey("immigration_programs.program_id"), nullable=False)
    category = Column(String(64), nullable=False)
    field = Column(String(128), nullable=False)
    operator = Column(String(16), nullable=False)
    value_json = Column(Text)
    unit = Column(String(32))
    mandatory = Column(Boolean, nullable=False, default=False)
    critical = Column(Boolean, nullable=False, default=False)
    message = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="DRAFT")
    current_version = Column(Integer, nullable=False, default=1)
    expression_json = Column(JSON)  # full expression tree (AND/OR/NOT/IF-THEN/UNLESS/REQUIRES)
    condition_json = Column(JSON)
    exception_json = Column(JSON)

    # provenance (inline — single-row, no FK needed for runtime)
    source_id = Column(String(32))
    document_id = Column(String(32))
    document_version = Column(Integer)
    chunk_id = Column(Integer)
    evidence_text = Column(Text)

    __table_args__ = (
        Index("idx_rules_program", "program_id"),
        Index("idx_rules_status", "status"),
        Index("idx_rules_requirement", "requirement_id"),
    )


class RuleVersion(Base, TimestampMixin):
    __tablename__ = "rule_versions"
    version_id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(String(48), ForeignKey("rules.rule_id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    snapshot_json = Column(JSON, nullable=False)
    change_type = Column(String(16), nullable=False)
    created_by = Column(String(64), nullable=False, default="system")

    __table_args__ = (
        UniqueConstraint("rule_id", "version_number", name="uq_rule_version"),
        Index("idx_rule_versions_rule", "rule_id"),
    )


# ===========================================================================
#  PHASE 3C — Applicant Profiles + Assessments
# ===========================================================================

class ApplicantProfile(Base, TimestampMixin):
    __tablename__ = "applicant_profiles"
    applicant_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("api_users.user_id"), nullable=False)
    first_name = Column(String(128))
    last_name = Column(String(128))
    date_of_birth = Column(DateTime(timezone=True))
    passport_nationality = Column(String(8))
    current_visa_subclass = Column(String(8))
    profile_json = Column(JSON, nullable=False, default=dict)
    # applicant-level fields cached at assessment time
    age = Column(Integer)
    english_level = Column(String(32))
    points = Column(Integer)
    occupation = Column(String(32))
    skills_assessment_status = Column(String(32))
    health_status = Column(String(32))
    character_status = Column(String(32))
    has_regional_nomination = Column(Boolean)
    visa_subclass = Column(String(8))

    __table_args__ = (Index("idx_applicant_user", "user_id"),)


class VisaSelection(Base, TimestampMixin):
    __tablename__ = "visa_selections"
    selection_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    applicant_id = Column(String(36), ForeignKey("applicant_profiles.applicant_id"), nullable=False)
    program_id = Column(String(32), ForeignKey("immigration_programs.program_id"), nullable=False)
    selected_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("applicant_id", "program_id", name="uq_applicant_program"),
        Index("idx_visa_selection_applicant", "applicant_id"),
    )


class Assessment(Base, TimestampMixin):
    __tablename__ = "assessments"
    assessment_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    applicant_id = Column(String(36), ForeignKey("applicant_profiles.applicant_id"), nullable=False)
    program_id = Column(String(32), ForeignKey("immigration_programs.program_id"), nullable=False)
    rule_version_id = Column(Integer)  # which rule version was used
    business_status = Column(String(16), nullable=False)  # ELIGIBLE/INELIGIBLE/INCOMPLETE
    overall_status = Column(String(16), nullable=False)   # raw engine status
    total_rules = Column(Integer, nullable=False)
    passed_count = Column(Integer, nullable=False)
    failed_count = Column(Integer, nullable=False)
    unknown_count = Column(Integer, nullable=False)
    not_applicable_count = Column(Integer, nullable=False)
    evaluated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    request_id = Column(String(36))  # correlation ID for request tracing

    __table_args__ = (
        Index("idx_assessment_applicant", "applicant_id"),
        Index("idx_assessment_program", "program_id"),
        Index("idx_assessment_date", "evaluated_at"),
    )


class AssessmentRuleResult(Base, TimestampMixin):
    __tablename__ = "assessment_rule_results"
    result_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    assessment_id = Column(String(36), ForeignKey("assessments.assessment_id"), nullable=False)
    rule_id = Column(String(48), ForeignKey("rules.rule_id"), nullable=False)
    rule_version = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False)  # PASS/FAIL/UNKNOWN/NOT_APPLICABLE
    field = Column(String(128))
    actual_value = Column(Text)
    expected_value = Column(Text)
    operator = Column(String(16))
    message = Column(Text)
    reason = Column(Text)

    __table_args__ = (
        Index("idx_assessment_result_assessment", "assessment_id"),
        Index("idx_assessment_result_rule", "rule_id"),
    )


class Gap(Base, TimestampMixin):
    __tablename__ = "gaps"
    gap_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    assessment_id = Column(String(36), ForeignKey("assessments.assessment_id"), nullable=False)
    field = Column(String(128), nullable=False)
    priority = Column(String(16), nullable=False)  # CRITICAL/HIGH/MEDIUM/LOW
    message = Column(Text, nullable=False)
    rule_id = Column(String(48))
    evidence_text = Column(Text)
    answer = Column(Text)
    source_chunk_ids = Column(JSON, default=list)

    __table_args__ = (Index("idx_gap_assessment", "assessment_id"),)


# ===========================================================================
#  PRODUCTION — Authentication + API + Audit
# ===========================================================================

class ApiUser(Base, TimestampMixin):
    __tablename__ = "api_users"
    user_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(256), unique=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    full_name = Column(String(256))
    role = Column(String(32), nullable=False, default="applicant")  # applicant/admin
    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime(timezone=True))

    __table_args__ = (Index("idx_user_email", "email"),)


class UserSession(Base, TimestampMixin):
    __tablename__ = "user_sessions"
    session_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("api_users.user_id"), nullable=False)
    token_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("idx_session_user", "user_id"),
        Index("idx_session_token", "token_hash"),
    )


class AuditLog(Base):
    """Append-only audit trail. Every state change gets a row."""
    __tablename__ = "audit_log"
    audit_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("api_users.user_id"))
    action = Column(String(64), nullable=False)
    entity_type = Column(String(64), nullable=False)
    entity_id = Column(String(64))
    before_json = Column(JSON)
    after_json = Column(JSON)
    request_id = Column(String(36))
    ip_address = Column(String(64))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        Index("idx_audit_user", "user_id"),
        Index("idx_audit_entity", "entity_type", "entity_id"),
        Index("idx_audit_created", "created_at"),
    )


class LlmUsage(Base):
    """Per-call LLM usage tracking."""
    __tablename__ = "llm_usage"
    usage_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    request_id = Column(String(36))
    user_id = Column(String(36), ForeignKey("api_users.user_id"))
    provider = Column(String(32), nullable=False)  # openai/gemini/anthropic
    model = Column(String(64), nullable=False)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Float)
    latency_ms = Column(Integer)
    success = Column(Boolean, nullable=False, default=True)
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        Index("idx_llm_usage_user", "user_id"),
        Index("idx_llm_usage_provider", "provider", "created_at"),
    )


class LlmBudgetLog(Base):
    """Daily/monthly budget aggregation."""
    __tablename__ = "llm_budget_log"
    log_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    period = Column(String(16), nullable=False)  # "daily" / "monthly"
    period_start = Column(DateTime(timezone=True), nullable=False)
    provider = Column(String(32))
    total_cost_usd = Column(Float, nullable=False, default=0.0)
    total_calls = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    budget_limit_usd = Column(Float)
    exceeded = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("idx_budget_period", "period", "period_start"),
    )
