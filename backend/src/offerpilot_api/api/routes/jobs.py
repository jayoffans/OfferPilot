"""Authenticated user-private job CRUD and evidence review, without URL fetches."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from offerpilot_api.core.api_errors import error_responses
from offerpilot_api.db.models import User
from offerpilot_api.db.session import get_db
from offerpilot_api.identity.dependencies import get_current_user
from offerpilot_api.job_catalog.schemas import (
    JobCreateRequest, JobDetailResponse, JobFavoriteRequest, JobListResponse,
    JobParsePreviewRequest, JobParsePreviewResponse, JobRequirementResponse,
    JobRequirementReviewRequest, JobRequirementWriteRequest, JobRequirementsSaveRequest,
    JobRevisionRequest, JobSaveRequest, WorkType,
)
from offerpilot_api.job_catalog.services import (
    create_job, delete_job, favorite_job, get_job, list_jobs, remove_requirement,
    review_requirement, save_job, save_requirements, write_requirement,
)
from offerpilot_api.job_intelligence.parser import parse_jd

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"], responses=error_responses(401, 403, 404, 409, 422, 503))
DB = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("/parse-preview", response_model=JobParsePreviewResponse)
def parse_preview(payload: JobParsePreviewRequest, current_user: CurrentUser):
    return parse_jd(payload.jd_text)


@router.post("", response_model=JobDetailResponse, status_code=201)
def create(payload: JobCreateRequest, db: DB, current_user: CurrentUser):
    return create_job(db, user_id=current_user.id, payload=payload)


@router.get("", response_model=JobListResponse)
def read_list(db: DB, current_user: CurrentUser,
              q: Annotated[str | None, Query(max_length=200)] = None,
              work_type: WorkType | None = None, is_favorite: bool | None = None,
              review_status: Literal["pending", "reviewed"] | None = None,
              sort: Literal["updated_desc", "created_desc", "title_asc"] = "updated_desc",
              page: Annotated[int, Query(ge=1, le=100000)] = 1,
              page_size: Annotated[int, Query(ge=1, le=100)] = 20):
    return list_jobs(db, user_id=current_user.id, q=q, work_type=work_type, is_favorite=is_favorite,
                     review_status=review_status, sort=sort, page=page, page_size=page_size)


@router.get("/{job_id}", response_model=JobDetailResponse)
def read_detail(job_id: UUID, db: DB, current_user: CurrentUser):
    return get_job(db, user_id=current_user.id, job_id=job_id)


@router.put("/{job_id}", response_model=JobDetailResponse)
def save(job_id: UUID, payload: JobSaveRequest, db: DB, current_user: CurrentUser):
    return save_job(db, user_id=current_user.id, job_id=job_id, payload=payload)


@router.delete("/{job_id}", status_code=204)
def remove(job_id: UUID, payload: JobRevisionRequest, db: DB, current_user: CurrentUser):
    delete_job(db, user_id=current_user.id, job_id=job_id, revision=payload.revision)
    return Response(status_code=204)


@router.patch("/{job_id}/favorite", response_model=JobDetailResponse)
def favorite(job_id: UUID, payload: JobFavoriteRequest, db: DB, current_user: CurrentUser):
    return favorite_job(db, user_id=current_user.id, job_id=job_id, payload=payload)


@router.get("/{job_id}/requirements", response_model=list[JobRequirementResponse])
def requirements(job_id: UUID, db: DB, current_user: CurrentUser):
    return get_job(db, user_id=current_user.id, job_id=job_id).requirements


@router.put("/{job_id}/requirements", response_model=JobDetailResponse)
def replace_requirements(job_id: UUID, payload: JobRequirementsSaveRequest, db: DB, current_user: CurrentUser):
    return save_requirements(db, user_id=current_user.id, job_id=job_id, payload=payload)


@router.post("/{job_id}/requirements", response_model=JobDetailResponse, status_code=201)
def add_requirement(job_id: UUID, payload: JobRequirementWriteRequest, db: DB, current_user: CurrentUser):
    return write_requirement(db, user_id=current_user.id, job_id=job_id, payload=payload)


@router.put("/{job_id}/requirements/{requirement_id}", response_model=JobDetailResponse)
def edit_requirement(job_id: UUID, requirement_id: UUID, payload: JobRequirementWriteRequest, db: DB, current_user: CurrentUser):
    return write_requirement(db, user_id=current_user.id, job_id=job_id, requirement_id=requirement_id, payload=payload)


@router.delete("/{job_id}/requirements/{requirement_id}", response_model=JobDetailResponse)
def delete_requirement(job_id: UUID, requirement_id: UUID, payload: JobRevisionRequest, db: DB, current_user: CurrentUser):
    return remove_requirement(db, user_id=current_user.id, job_id=job_id, requirement_id=requirement_id, revision=payload.revision)


@router.post("/{job_id}/requirements/{requirement_id}/review", response_model=JobDetailResponse)
def review(job_id: UUID, requirement_id: UUID, payload: JobRequirementReviewRequest, db: DB, current_user: CurrentUser):
    return review_requirement(db, user_id=current_user.id, job_id=job_id, requirement_id=requirement_id, payload=payload)
