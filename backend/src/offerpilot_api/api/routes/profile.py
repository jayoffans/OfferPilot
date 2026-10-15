"""Authenticated reviewed-profile API; extraction ingestion has no HTTP route."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from offerpilot_api.core.api_errors import error_responses
from offerpilot_api.db.models import User
from offerpilot_api.db.session import get_db
from offerpilot_api.identity.dependencies import get_current_user
from offerpilot_api.profile.schemas import CareerProfileResponse, ProfileCandidateReviewRequest, ProfileSaveRequest, ProfilePreferencesSaveRequest
from offerpilot_api.profile.services import get_profile, review_profile_candidate, save_profile, save_preferences

router = APIRouter(prefix="/api/v1/profile", tags=["profile"], responses=error_responses(401, 403, 404, 409, 422, 503))


@router.get("", response_model=CareerProfileResponse)
def read_current_profile(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CareerProfileResponse:
    return get_profile(db, user_id=current_user.id)


@router.put("", response_model=CareerProfileResponse)
def write_current_profile(
    payload: ProfileSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CareerProfileResponse:
    return save_profile(db, user_id=current_user.id, payload=payload)


@router.post("/candidates/{candidate_id}/review", response_model=CareerProfileResponse)
def review_current_candidate(
    candidate_id: UUID,
    payload: ProfileCandidateReviewRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CareerProfileResponse:
    return review_profile_candidate(db, user_id=current_user.id, candidate_id=candidate_id, payload=payload)


@router.put("/preferences", response_model=CareerProfileResponse)
def write_preferences(
    payload: ProfilePreferencesSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CareerProfileResponse:
    return save_preferences(db, user_id=current_user.id, payload=payload)
