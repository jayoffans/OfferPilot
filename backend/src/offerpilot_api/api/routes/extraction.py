"""Authenticated, opt-in synthetic extraction API; no provider/key input."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from offerpilot_api.agent_runtime.extraction_runs import (
    create_extraction_run, get_extraction_capability, get_extraction_run,
    get_run_candidates, mutate_extraction_run,
)
from offerpilot_api.agent_runtime.extraction_schemas import (
    ExtractionCapabilityResponse, ExtractionRunCandidatesResponse, ExtractionRunCreateRequest,
    ExtractionRunMutationRequest, ExtractionRunResponse,
)
from offerpilot_api.core.api_errors import error_responses
from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.models import User
from offerpilot_api.db.session import get_db
from offerpilot_api.identity.dependencies import get_current_user

router = APIRouter(prefix="/api/v1", tags=["resume-extraction"], responses=error_responses(401, 403, 404, 409, 422, 429, 503))


@router.get("/resumes/{resume_id}/extraction", response_model=ExtractionCapabilityResponse)
def capability(resume_id: UUID, db: Annotated[Session, Depends(get_db)],
               current_user: Annotated[User, Depends(get_current_user)], settings: Annotated[Settings, Depends(get_settings)]) -> ExtractionCapabilityResponse:
    return get_extraction_capability(db, user_id=current_user.id, resume_id=resume_id, settings=settings)


@router.post("/resumes/{resume_id}/extraction-runs", response_model=ExtractionRunResponse, status_code=202)
def create_run(resume_id: UUID, payload: ExtractionRunCreateRequest, db: Annotated[Session, Depends(get_db)],
               current_user: Annotated[User, Depends(get_current_user)], settings: Annotated[Settings, Depends(get_settings)]) -> ExtractionRunResponse:
    return create_extraction_run(db, user_id=current_user.id, resume_id=resume_id, payload=payload, settings=settings)


@router.get("/extraction-runs/{run_id}", response_model=ExtractionRunResponse)
def read_run(run_id: UUID, db: Annotated[Session, Depends(get_db)], current_user: Annotated[User, Depends(get_current_user)]) -> ExtractionRunResponse:
    return get_extraction_run(db, user_id=current_user.id, run_id=run_id)


@router.get("/extraction-runs/{run_id}/candidates", response_model=ExtractionRunCandidatesResponse)
def read_candidates(run_id: UUID, db: Annotated[Session, Depends(get_db)], current_user: Annotated[User, Depends(get_current_user)]) -> ExtractionRunCandidatesResponse:
    return get_run_candidates(db, user_id=current_user.id, run_id=run_id)


@router.post("/extraction-runs/{run_id}/cancel", response_model=ExtractionRunResponse)
def cancel_run(run_id: UUID, payload: ExtractionRunMutationRequest, db: Annotated[Session, Depends(get_db)],
               current_user: Annotated[User, Depends(get_current_user)], settings: Annotated[Settings, Depends(get_settings)]) -> ExtractionRunResponse:
    return mutate_extraction_run(db, user_id=current_user.id, run_id=run_id, revision=payload.revision, action="cancel", settings=settings)


@router.post("/extraction-runs/{run_id}/retry", response_model=ExtractionRunResponse)
def retry_run(run_id: UUID, payload: ExtractionRunMutationRequest, db: Annotated[Session, Depends(get_db)],
              current_user: Annotated[User, Depends(get_current_user)], settings: Annotated[Settings, Depends(get_settings)]) -> ExtractionRunResponse:
    return mutate_extraction_run(db, user_id=current_user.id, run_id=run_id, revision=payload.revision, action="retry", settings=settings)
