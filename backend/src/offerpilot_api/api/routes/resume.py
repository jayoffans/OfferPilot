"""Authenticated resume upload and user-scoped metadata endpoints."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy.orm import Session

from offerpilot_api.core.api_errors import error_responses
from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.models import User
from offerpilot_api.db.session import get_db
from offerpilot_api.identity.dependencies import get_current_user
from offerpilot_api.resume_ingestion.access import (
    ResumeAccessError,
    get_owned_profile,
    get_owned_resume,
    list_owned_profile_facts,
    list_owned_resumes,
    rename_owned_resume,
)
from offerpilot_api.resume_ingestion.lifecycle import ResumeLifecycleError, delete_owned_resume, download_owned_resume, get_owned_lifecycle
from offerpilot_api.resume_ingestion.storage import LocalResumeStorage, ResumeStorage, StorageError
from offerpilot_api.services.resume import MAX_PDF_BYTES, ResumeUploadError, normalize_pdf_filename, save_resume_document

router = APIRouter(tags=["resume"], responses=error_responses(401, 403, 404, 409, 410, 422, 500, 503))


def get_resume_storage(settings: Annotated[Settings, Depends(get_settings)]) -> ResumeStorage:
    try:
        return LocalResumeStorage(settings.upload_dir, max_bytes=MAX_PDF_BYTES)
    except StorageError:
        raise ResumeLifecycleError("resume_storage_unavailable", "文件存储暂时不可用，请联系维护者", 503) from None


class ResumeUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_name: str
    file_type: str
    created_at: datetime


class ResumeRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str = Field(min_length=1, max_length=255)


class ResumeLifecycleResponse(BaseModel):
    id: UUID
    processing_status: Literal["uploading", "uploaded", "parsed", "failed"] | None
    lifecycle_status: Literal["active", "deleting", "delete_failed", "deleted"]
    deletion_status: Literal["deleting", "delete_failed", "deleted"] | None
    error_code: str | None
    attempt_count: int = Field(ge=0)


class ResumeProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    resume_id: UUID
    name: str | None
    phone: str | None
    email: str | None
    education: list[dict[str, JsonValue]]
    skills: list[str]
    projects: list[dict[str, JsonValue]]
    internships: list[dict[str, JsonValue]]
    created_at: datetime


class ResumeFactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    profile_id: UUID
    field_name: str
    value: JsonValue
    confidence: float
    source_text: str
    created_at: datetime


@router.post("/resume/upload", response_model=ResumeUploadResponse, status_code=status.HTTP_201_CREATED)
@router.post("/api/v1/resumes", response_model=ResumeUploadResponse, status_code=status.HTTP_201_CREATED)
def upload_resume(
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[ResumeStorage, Depends(get_resume_storage)],
) -> ResumeUploadResponse:
    if settings.environment != "development":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Resume upload is available only in local development",
        )
    try:
        document = save_resume_document(file, db, settings.upload_dir, user_id=current_user.id, storage=storage)
    except ResumeUploadError as exc:
        headers = {"Location": f"/api/v1/resumes/{exc.resume_id}/lifecycle"} if exc.resume_id else None
        detail = {"code": exc.code, "message": exc.detail} if exc.code else exc.detail
        raise HTTPException(status_code=exc.status_code, detail=detail, headers=headers) from None

    return ResumeUploadResponse(
        id=document.id,
        file_name=document.file_name,
        file_type=document.file_type,
        created_at=document.created_at,
    )


@router.get("/api/v1/resumes", response_model=list[ResumeUploadResponse])
def list_resumes(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[ResumeUploadResponse]:
    return [
        ResumeUploadResponse.model_validate(document)
        for document in list_owned_resumes(db, user_id=current_user.id)
    ]


@router.get("/api/v1/resumes/{resume_id}", response_model=ResumeUploadResponse)
def get_resume(
    resume_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ResumeUploadResponse:
    try:
        document = get_owned_resume(db, resume_id=resume_id, user_id=current_user.id)
    except ResumeAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return ResumeUploadResponse.model_validate(document)


@router.patch("/api/v1/resumes/{resume_id}", response_model=ResumeUploadResponse)
def rename_resume(
    resume_id: UUID,
    payload: ResumeRenameRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ResumeUploadResponse:
    try:
        document = rename_owned_resume(
            db, resume_id=resume_id, user_id=current_user.id, file_name=payload.file_name
        )
    except ResumeAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return ResumeUploadResponse.model_validate(document)


@router.get("/api/v1/resumes/{resume_id}/profile", response_model=ResumeProfileResponse)
def get_resume_profile(
    resume_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ResumeProfileResponse:
    try:
        profile = get_owned_profile(db, resume_id=resume_id, user_id=current_user.id)
    except ResumeAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return ResumeProfileResponse.model_validate(profile)


@router.get("/api/v1/resumes/{resume_id}/profile/facts", response_model=list[ResumeFactResponse])
def get_resume_profile_facts(
    resume_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[ResumeFactResponse]:
    try:
        facts = list_owned_profile_facts(db, resume_id=resume_id, user_id=current_user.id)
    except ResumeAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return [ResumeFactResponse.model_validate(fact) for fact in facts]


@router.get(
    "/api/v1/resumes/{resume_id}/download", response_class=Response,
    responses={200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}},
)
def download_resume(
    resume_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[ResumeStorage, Depends(get_resume_storage)],
) -> Response:
    downloaded = download_owned_resume(db, resume_id=resume_id, user_id=current_user.id, storage=storage)
    try:
        display_name = normalize_pdf_filename(downloaded.file_name)
    except ResumeUploadError:
        display_name = "resume.pdf"
    # All displayed UTF-8 bytes are percent encoded. No path or object key is
    # exposed and no user-provided character becomes raw header syntax.
    return Response(
        content=downloaded.content, media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=\"resume.pdf\"; filename*=UTF-8''{quote(display_name, safe='')}",
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/api/v1/resumes/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume(
    resume_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[ResumeStorage, Depends(get_resume_storage)],
) -> Response:
    delete_owned_resume(db, resume_id=resume_id, user_id=current_user.id, storage=storage)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/api/v1/resumes/{resume_id}/lifecycle", response_model=ResumeLifecycleResponse)
def resume_lifecycle(
    resume_id: UUID,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ResumeLifecycleResponse:
    response.headers["Cache-Control"] = "no-store"
    return ResumeLifecycleResponse(**get_owned_lifecycle(db, resume_id=resume_id, user_id=current_user.id))
