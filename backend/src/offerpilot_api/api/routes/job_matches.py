"""Owner-scoped deterministic match actions; no client facts are accepted."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from offerpilot_api.core.api_errors import error_responses
from offerpilot_api.db.models import User
from offerpilot_api.db.session import get_db
from offerpilot_api.identity.dependencies import get_current_user
from offerpilot_api.job_intelligence.match_api_schemas import JobMatchCreateRequest, JobMatchHistoryResponse, JobMatchResponse
from offerpilot_api.job_intelligence.match_services import create_match, get_match, latest_match, match_history

router = APIRouter(prefix="/api/v1/jobs/{job_id}/matches", tags=["job-matches"], responses=error_responses(401, 403, 404, 409, 422, 503))
DB = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("", response_model=JobMatchResponse)
def calculate(job_id: UUID, payload: JobMatchCreateRequest, db: DB, current_user: CurrentUser):
    return create_match(db, user_id=current_user.id, job_id=job_id, payload=payload)


@router.get("", response_model=JobMatchHistoryResponse)
def history(job_id: UUID, db: DB, current_user: CurrentUser, limit: Annotated[int, Query(ge=1, le=50)] = 20):
    return match_history(db, user_id=current_user.id, job_id=job_id, limit=limit)


@router.get("/latest", response_model=JobMatchResponse | None)
def latest(job_id: UUID, db: DB, current_user: CurrentUser):
    return latest_match(db, user_id=current_user.id, job_id=job_id)


@router.get("/{match_id}", response_model=JobMatchResponse)
def detail(job_id: UUID, match_id: UUID, db: DB, current_user: CurrentUser):
    return get_match(db, user_id=current_user.id, job_id=job_id, match_id=match_id)
