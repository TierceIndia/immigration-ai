"""Repository pattern for database access.

Usage:
    from db.connection import get_engine, get_session
    from db.repositories import ApplicantRepo, AssessmentRepo

    engine = get_engine()
    with get_session(engine) as session:
        repo = ApplicantRepo(session)
        profile = repo.create(user_id="...", visa_program="SKILLED_189")
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from db.models import (
    ApplicantProfile as ApplicantProfileModel,
    Assessment,
    AssessmentRuleResult,
    AuditLog,
    Country,
    DocumentChunk,
    ExtractionRun,
    Gap,
    GovernmentDocument,
    GovernmentSource,
    ImmigrationProgram,
    LlmUsage,
    PipelineRun,
    Requirement,
    RequirementEvidence,
    Rule,
    RuleVersion,
    VisaSelection as VisaSelectionModel,
    RequirementVersion,
)


class CountryRepo:
    def __init__(self, session: Session):
        self._s = session

    def get(self, country_id: str) -> Country | None:
        return self._s.get(Country, country_id)

    def upsert(self, country_id: str, name: str, iso_code: str) -> Country:
        obj = self._s.get(Country, country_id)
        if obj is None:
            obj = Country(country_id=country_id, name=name, iso_code=iso_code)
            self._s.add(obj)
        else:
            obj.name = name
            obj.iso_code = iso_code
        return obj


class ProgramRepo:
    def __init__(self, session: Session):
        self._s = session

    def get(self, program_id: str) -> ImmigrationProgram | None:
        return self._s.get(ImmigrationProgram, program_id)

    def list_by_country(self, country_id: str) -> list[ImmigrationProgram]:
        stmt = select(ImmigrationProgram).where(ImmigrationProgram.country_id == country_id)
        return list(self._s.execute(stmt).scalars())

    def upsert(self, program_id: str, country_id: str, visa_category: str,
               subclass_code: str | None, name: str, status: str = "ACTIVE") -> ImmigrationProgram:
        obj = self._s.get(ImmigrationProgram, program_id)
        if obj is None:
            obj = ImmigrationProgram(
                program_id=program_id, country_id=country_id,
                visa_category=visa_category, subclass_code=subclass_code,
                name=name, status=status,
            )
            self._s.add(obj)
        else:
            obj.visa_category = visa_category
            obj.subclass_code = subclass_code
            obj.name = name
            obj.status = status
        return obj


class SourceRepo:
    def __init__(self, session: Session):
        self._s = session

    def get(self, source_id: str) -> GovernmentSource | None:
        return self._s.get(GovernmentSource, source_id)

    def list_by_program(self, program_id: str) -> list[GovernmentSource]:
        stmt = select(GovernmentSource).where(GovernmentSource.program_id == program_id)
        return list(self._s.execute(stmt).scalars())

    def create(self, **kwargs) -> GovernmentSource:
        obj = GovernmentSource(**kwargs)
        self._s.add(obj)
        return obj

    def update_status(self, source_id: str, status: str, last_hash: str | None = None,
                      last_checked_at: datetime | None = None) -> GovernmentSource | None:
        obj = self._s.get(GovernmentSource, source_id)
        if obj:
            obj.status = status
            if last_hash is not None:
                obj.last_hash = last_hash
            if last_checked_at is not None:
                obj.last_checked_at = last_checked_at
        return obj


class DocumentRepo:
    def __init__(self, session: Session):
        self._s = session

    def get(self, document_id: str) -> GovernmentDocument | None:
        return self._s.get(GovernmentDocument, document_id)

    def create(self, **kwargs) -> GovernmentDocument:
        obj = GovernmentDocument(**kwargs)
        self._s.add(obj)
        return obj

    def count_chunks(self, document_id: str) -> int:
        stmt = select(func.count()).where(DocumentChunk.document_id == document_id)
        return self._s.execute(stmt).scalar() or 0


class ChunkRepo:
    def __init__(self, session: Session):
        self._s = session

    def create(self, **kwargs) -> DocumentChunk:
        obj = DocumentChunk(**kwargs)
        self._s.add(obj)
        return obj

    def list_for_document(self, document_id: str, version_id: int) -> list[DocumentChunk]:
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id, DocumentChunk.version_id == version_id)
            .order_by(DocumentChunk.order_index)
        )
        return list(self._s.execute(stmt).scalars())


class ExtractionRunRepo:
    def __init__(self, session: Session):
        self._s = session

    def create(self, run_id: str, **kwargs) -> ExtractionRun:
        obj = ExtractionRun(run_id=run_id, **kwargs)
        self._s.add(obj)
        return obj

    def get(self, run_id: str) -> ExtractionRun | None:
        return self._s.get(ExtractionRun, run_id)

    def update_status(self, run_id: str, **kwargs) -> ExtractionRun | None:
        obj = self._s.get(ExtractionRun, run_id)
        if obj:
            for k, v in kwargs.items():
                setattr(obj, k, v)
        return obj


class RequirementRepo:
    def __init__(self, session: Session):
        self._s = session

    def get(self, requirement_id: str) -> Requirement | None:
        return self._s.get(Requirement, requirement_id)

    def create(self, requirement_id: str, **kwargs) -> Requirement:
        obj = Requirement(requirement_id=requirement_id, **kwargs)
        self._s.add(obj)
        return obj

    def list_by_program(self, program_id: str, status: str | None = None) -> list[Requirement]:
        stmt = select(Requirement).where(Requirement.program_id == program_id)
        if status:
            stmt = stmt.where(Requirement.status == status)
        return list(self._s.execute(stmt).scalars())

    def update_status(self, requirement_id: str, status: str) -> Requirement | None:
        obj = self._s.get(Requirement, requirement_id)
        if obj:
            obj.status = status
        return obj

    def save_version(self, requirement_id: str, version_number: int, snapshot_json: str,
                     document_version: int, change_type: str, created_by: str = "system") -> RequirementVersion:
        rv = RequirementVersion(
            requirement_id=requirement_id,
            version_number=version_number,
            snapshot_json=snapshot_json,
            document_version=document_version,
            change_type=change_type,
            created_by=created_by,
        )
        self._s.add(rv)
        return rv


class EvidenceRepo:
    def __init__(self, session: Session):
        self._s = session

    def create(self, **kwargs) -> RequirementEvidence:
        obj = RequirementEvidence(**kwargs)
        self._s.add(obj)
        return obj

    def list_for_requirement(self, requirement_id: str) -> list[RequirementEvidence]:
        stmt = select(RequirementEvidence).where(RequirementEvidence.requirement_id == requirement_id)
        return list(self._s.execute(stmt).scalars())


class RuleRepo:
    def __init__(self, session: Session):
        self._s = session

    def get(self, rule_id: str) -> Rule | None:
        return self._s.get(Rule, rule_id)

    def create(self, rule_id: str, **kwargs) -> Rule:
        obj = Rule(rule_id=rule_id, **kwargs)
        self._s.add(obj)
        return obj

    def list_active(self, program_id: str | None = None) -> list[Rule]:
        stmt = select(Rule).where(Rule.status == "ACTIVE")
        if program_id:
            stmt = stmt.where(Rule.program_id == program_id)
        return list(self._s.execute(stmt).scalars())

    def update_status(self, rule_id: str, status: str) -> Rule | None:
        obj = self._s.get(Rule, rule_id)
        if obj:
            obj.status = status
        return obj

    def count(self) -> int:
        return self._s.execute(select(func.count()).select_from(Rule)).scalar() or 0

    def save_version(self, rule_id: str, version_number: int, snapshot_json: dict,
                     change_type: str, created_by: str = "system") -> RuleVersion:
        rv = RuleVersion(
            rule_id=rule_id,
            version_number=version_number,
            snapshot_json=snapshot_json,
            change_type=change_type,
            created_by=created_by,
        )
        self._s.add(rv)
        return rv


class UserRepo:
    def __init__(self, session: Session):
        self._s = session

    def get(self, user_id: str):
        from db.models import ApiUser
        return self._s.get(ApiUser, user_id)

    def get_by_email(self, email: str):
        from db.models import ApiUser
        stmt = select(ApiUser).where(ApiUser.email == email)
        return self._s.execute(stmt).scalars().first()

    def create(self, email: str, password_hash: str, full_name: str | None = None,
               role: str = "applicant"):
        from db.models import ApiUser
        obj = ApiUser(email=email, password_hash=password_hash,
                      full_name=full_name, role=role)
        self._s.add(obj)
        self._s.flush()
        return obj


class ApplicantRepo:
    def __init__(self, session: Session):
        self._s = session

    def get(self, applicant_id: str) -> ApplicantProfileModel | None:
        return self._s.get(ApplicantProfileModel, applicant_id)

    def create(self, user_id: str, **kwargs) -> ApplicantProfileModel:
        obj = ApplicantProfileModel(user_id=user_id, **kwargs)
        self._s.add(obj)
        self._s.flush()  # populate applicant_id
        return obj

    def list_for_user(self, user_id: str) -> list[ApplicantProfileModel]:
        stmt = select(ApplicantProfileModel).where(ApplicantProfileModel.user_id == user_id)
        return list(self._s.execute(stmt).scalars())

    def update(self, applicant_id: str, **kwargs) -> ApplicantProfileModel | None:
        obj = self._s.get(ApplicantProfileModel, applicant_id)
        if obj:
            for k, v in kwargs.items():
                setattr(obj, k, v)
        return obj


class VisaSelectionRepo:
    def __init__(self, session: Session):
        self._s = session

    def create(self, applicant_id: str, program_id: str) -> VisaSelectionModel:
        obj = VisaSelectionModel(applicant_id=applicant_id, program_id=program_id)
        self._s.add(obj)
        return obj

    def list_for_applicant(self, applicant_id: str) -> list[VisaSelectionModel]:
        stmt = select(VisaSelectionModel).where(VisaSelectionModel.applicant_id == applicant_id)
        return list(self._s.execute(stmt).scalars())


class AssessmentRepo:
    def __init__(self, session: Session):
        self._s = session

    def get(self, assessment_id: str) -> Assessment | None:
        return self._s.get(Assessment, assessment_id)

    def create(self, applicant_id: str, program_id: str, **kwargs) -> Assessment:
        obj = Assessment(applicant_id=applicant_id, program_id=program_id, **kwargs)
        self._s.add(obj)
        self._s.flush()
        return obj

    def list_for_applicant(self, applicant_id: str, limit: int = 20) -> list[Assessment]:
        stmt = (
            select(Assessment)
            .where(Assessment.applicant_id == applicant_id)
            .order_by(Assessment.evaluated_at.desc())
            .limit(limit)
        )
        return list(self._s.execute(stmt).scalars())

    def list_for_program(self, program_id: str, limit: int = 100) -> list[Assessment]:
        stmt = (
            select(Assessment)
            .where(Assessment.program_id == program_id)
            .order_by(Assessment.evaluated_at.desc())
            .limit(limit)
        )
        return list(self._s.execute(stmt).scalars())


class AssessmentRuleResultRepo:
    def __init__(self, session: Session):
        self._s = session

    def create(self, assessment_id: str, **kwargs) -> AssessmentRuleResult:
        obj = AssessmentRuleResult(assessment_id=assessment_id, **kwargs)
        self._s.add(obj)
        return obj

    def list_for_assessment(self, assessment_id: str) -> list[AssessmentRuleResult]:
        stmt = select(AssessmentRuleResult).where(AssessmentRuleResult.assessment_id == assessment_id)
        return list(self._s.execute(stmt).scalars())


class GapRepo:
    def __init__(self, session: Session):
        self._s = session

    def create(self, assessment_id: str, **kwargs) -> Gap:
        obj = Gap(assessment_id=assessment_id, **kwargs)
        self._s.add(obj)
        return obj

    def list_for_assessment(self, assessment_id: str) -> list[Gap]:
        stmt = select(Gap).where(Gap.assessment_id == assessment_id)
        return list(self._s.execute(stmt).scalars())


class AuditRepo:
    def __init__(self, session: Session):
        self._s = session

    def log(self, user_id: str | None, action: str, entity_type: str,
            entity_id: str | None = None, before_json: dict | None = None,
            after_json: dict | None = None, request_id: str | None = None,
            ip_address: str | None = None) -> AuditLog:
        obj = AuditLog(
            user_id=user_id, action=action, entity_type=entity_type,
            entity_id=entity_id, before_json=before_json, after_json=after_json,
            request_id=request_id, ip_address=ip_address,
        )
        self._s.add(obj)
        return obj


class LlmUsageRepo:
    def __init__(self, session: Session):
        self._s = session

    def create(self, **kwargs) -> LlmUsage:
        obj = LlmUsage(**kwargs)
        self._s.add(obj)
        return obj

    def daily_cost(self, provider: str | None = None, date: datetime | None = None) -> float:
        """Sum cost_usd for today (or given date)."""
        if date is None:
            date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = select(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0)).where(
            LlmUsage.created_at >= date
        )
        if provider:
            stmt = stmt.where(LlmUsage.provider == provider)
        return float(self._s.execute(stmt).scalar())


class PipelineRunRepo:
    def __init__(self, session: Session):
        self._s = session

    def create(self, source_id: str, trigger_type: str, **kwargs) -> PipelineRun:
        obj = PipelineRun(source_id=source_id, trigger_type=trigger_type, **kwargs)
        self._s.add(obj)
        self._s.flush()
        return obj

    def get(self, run_id: str) -> PipelineRun | None:
        return self._s.get(PipelineRun, run_id)

    def _transition(self, obj: PipelineRun, new_status: str, error: str | None = None, **metrics) -> PipelineRun:
        from pipeline_run_activation import validate_run_transition
        validate_run_transition(obj.status, new_status)
        obj.status = new_status
        obj.completed_at = datetime.now(timezone.utc)
        if obj.started_at:
            delta = obj.completed_at - obj.started_at
            obj.duration_ms = int(delta.total_seconds() * 1000)
        if error is not None:
            existing = obj.errors or []
            existing.append(error)
            obj.errors = existing
        for k, v in metrics.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        self._s.flush()
        return obj

    def complete(self, run_id: str, **metrics) -> PipelineRun:
        obj = self._s.get(PipelineRun, run_id)
        if not obj:
            raise ValueError(f"PipelineRun {run_id} not found")
        return self._transition(obj, "COMPLETED", **metrics)

    def fail(self, run_id: str, error: str) -> PipelineRun:
        obj = self._s.get(PipelineRun, run_id)
        if not obj:
            raise ValueError(f"PipelineRun {run_id} not found")
        return self._transition(obj, "FAILED", error=error)

    def partial(self, run_id: str, error: str, **metrics) -> PipelineRun:
        """Mark run as PARTIAL (completed with some errors)."""
        obj = self._s.get(PipelineRun, run_id)
        if not obj:
            raise ValueError(f"PipelineRun {run_id} not found")
        return self._transition(obj, "PARTIAL", error=error, **metrics)

    def list_all(self, limit: int = 50, offset: int = 0) -> list[PipelineRun]:
        stmt = (
            select(PipelineRun)
            .order_by(PipelineRun.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self._s.execute(stmt).scalars())

    def list_for_source(self, source_id: str, limit: int = 20) -> list[PipelineRun]:
        stmt = (
            select(PipelineRun)
            .where(PipelineRun.source_id == source_id)
            .order_by(PipelineRun.started_at.desc())
            .limit(limit)
        )
        return list(self._s.execute(stmt).scalars())

    def count_by_status(self) -> dict[str, int]:
        stmt = select(PipelineRun.status, func.count()).group_by(PipelineRun.status)
        return {row[0]: row[1] for row in self._s.execute(stmt)}
