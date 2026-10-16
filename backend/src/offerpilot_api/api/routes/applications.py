"""Authenticated manual application tracking; no recruitment-site actions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from offerpilot_api.application_tracker.schemas import (
    ApplicationCreateRequest, ApplicationDeleteRequest, ApplicationEventListResponse,
    ApplicationListResponse, ApplicationResponse, ApplicationSort, ApplicationStatus,
    ApplicationUpdateRequest, NextActionFilter,
)
from offerpilot_api.application_tracker.services import (
    create_application, delete_application, get_application, list_applications, list_events, update_application,
)
from offerpilot_api.core.api_errors import error_responses
from offerpilot_api.db.models import User
from offerpilot_api.db.session import get_db
from offerpilot_api.identity.dependencies import get_current_user

router = APIRouter(prefix="/api/v1/applications", tags=["applications"], responses=error_responses(401, 403, 404, 409, 422, 503))
DB = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("", response_model=ApplicationResponse, status_code=201)
def create(payload: ApplicationCreateRequest, db: DB, current_user: CurrentUser):
    return create_application(db, user_id=current_user.id, payload=payload)


@router.get("", response_model=ApplicationListResponse)
def read_list(db: DB, current_user: CurrentUser,
              q: Annotated[str | None, Query(max_length=200)] = None,
              status: ApplicationStatus | None = None, job_id: UUID | None = None,
              next_action: NextActionFilter | None = None, overdue: bool | None = None,
              sort: ApplicationSort = "updated_desc",
              page: Annotated[int, Query(ge=1, le=100000)] = 1,
              page_size: Annotated[int, Query(ge=1, le=100)] = 20):
    return list_applications(db, user_id=current_user.id, q=q, status=status, job_id=job_id,
                             next_action=next_action, overdue=overdue, sort=sort, page=page, page_size=page_size)


@router.get("/{application_id}", response_model=ApplicationResponse)
def detail(application_id: UUID, db: DB, current_user: CurrentUser):
    return get_application(db, user_id=current_user.id, application_id=application_id)


@router.put("/{application_id}", response_model=ApplicationResponse)
def save(application_id: UUID, payload: ApplicationUpdateRequest, db: DB, current_user: CurrentUser):
    return update_application(db, user_id=current_user.id, application_id=application_id, payload=payload)


@router.delete("/{application_id}", status_code=204)
def remove(application_id: UUID, payload: ApplicationDeleteRequest, db: DB, current_user: CurrentUser):
    delete_application(db, user_id=current_user.id, application_id=application_id, payload=payload)
    return Response(status_code=204)


@router.get("/{application_id}/events", response_model=ApplicationEventListResponse)
def events(application_id: UUID, db: DB, current_user: CurrentUser,
           page: Annotated[int, Query(ge=1, le=100000)] = 1,
           page_size: Annotated[int, Query(ge=1, le=100)] = 20):
    return list_events(db, user_id=current_user.id, application_id=application_id, page=page, page_size=page_size)
