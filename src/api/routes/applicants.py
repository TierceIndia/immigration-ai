"""Applicant routes — CRUD for applicant profiles."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from api.routes.auth import get_current_user_id
from api.schemas.models import ApplicantCreate, ApplicantResponse, ApplicantUpdate
from db.connection import get_session
from logging_config import get_logger
from services.applicant_service import ApplicantService

router = APIRouter()
logger = get_logger(__name__)


@router.post("", response_model=ApplicantResponse, status_code=201)
def create_applicant(
    body: ApplicantCreate,
    request: Request,
    user_id: str = Depends(get_current_user_id),
):
    with get_session(request.app.state.db_engine) as session:
        svc = ApplicantService(session)
        return svc.create(user_id, **body.model_dump(exclude_unset=True))


@router.get("", response_model=list[ApplicantResponse])
def list_applicants(request: Request, user_id: str = Depends(get_current_user_id)):
    with get_session(request.app.state.db_engine) as session:
        svc = ApplicantService(session)
        return svc.list_for_user(user_id)


@router.get("/{applicant_id}", response_model=ApplicantResponse)
def get_applicant(applicant_id: str, request: Request):
    with get_session(request.app.state.db_engine) as session:
        svc = ApplicantService(session)
        result = svc.get(applicant_id)
        if not result:
            raise HTTPException(status_code=404, detail="Applicant not found")
        return result


@router.patch("/{applicant_id}", response_model=ApplicantResponse)
def update_applicant(applicant_id: str, body: ApplicantUpdate, request: Request):
    with get_session(request.app.state.db_engine) as session:
        svc = ApplicantService(session)
        result = svc.update(applicant_id, **body.model_dump(exclude_unset=True))
        if not result:
            raise HTTPException(status_code=404, detail="Applicant not found")
        return result
