"""Applicant service — business logic for applicant CRUD."""
from __future__ import annotations

from db.repositories import ApplicantRepo, VisaSelectionRepo
from logging_config import get_logger

logger = get_logger(__name__)


class ApplicantService:
    def __init__(self, session):
        self._repo = ApplicantRepo(session)
        self._visa_repo = VisaSelectionRepo(session)

    def create(self, user_id: str, **kwargs) -> dict:
        profile = self._repo.create(user_id, **kwargs)
        logger.info("Applicant created", extra={"applicant_id": profile.applicant_id})
        return self.get(profile.applicant_id)

    def get(self, applicant_id: str) -> dict | None:
        profile = self._repo.get(applicant_id)
        if not profile:
            return None
        return {
            "applicant_id": profile.applicant_id,
            "user_id": profile.user_id,
            "first_name": profile.first_name,
            "last_name": profile.last_name,
            "age": profile.age,
            "english_level": profile.english_level,
            "points": profile.points,
            "occupation": profile.occupation,
            "skills_assessment_status": profile.skills_assessment_status,
            "health_status": profile.health_status,
            "character_status": profile.character_status,
            "has_regional_nomination": profile.has_regional_nomination,
            "visa_subclass": profile.visa_subclass,
            "created_at": profile.created_at,
        }

    def list_for_user(self, user_id: str) -> list[dict]:
        profiles = self._repo.list_for_user(user_id)
        return [
            {
                "applicant_id": p.applicant_id,
                "user_id": p.user_id,
                "first_name": p.first_name,
                "last_name": p.last_name,
                "age": p.age,
                "english_level": p.english_level,
                "points": p.points,
                "occupation": p.occupation,
                "skills_assessment_status": p.skills_assessment_status,
                "health_status": p.health_status,
                "character_status": p.character_status,
                "has_regional_nomination": p.has_regional_nomination,
                "visa_subclass": p.visa_subclass,
                "created_at": p.created_at,
            }
            for p in profiles
        ]

    def update(self, applicant_id: str, **kwargs) -> dict | None:
        profile = self._repo.update(applicant_id, **kwargs)
        if not profile:
            return None
        logger.info("Applicant updated", extra={"applicant_id": applicant_id})
        return self.get(applicant_id)
