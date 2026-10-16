"""Authenticated, read-only workbench using existing domain data."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from offerpilot_api.application_tracker.dashboard import get_dashboard
from offerpilot_api.application_tracker.schemas import DashboardResponse
from offerpilot_api.core.api_errors import error_responses
from offerpilot_api.db.models import User
from offerpilot_api.db.session import get_db
from offerpilot_api.identity.dependencies import get_current_user

router = APIRouter(prefix="/api/v1/dashboard", tags=["applications"], responses=error_responses(401, 403, 409, 422, 503))


@router.get("", response_model=DashboardResponse)
def dashboard(db: Annotated[Session, Depends(get_db)], current_user: Annotated[User, Depends(get_current_user)],
              limit: Annotated[int, Query(ge=1, le=20)] = 5):
    return get_dashboard(db, user_id=current_user.id, limit=limit)
