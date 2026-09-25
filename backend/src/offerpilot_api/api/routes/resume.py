"""Resume document upload endpoint."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.session import get_db
from offerpilot_api.services.resume import ResumeUploadError, save_resume_document

router = APIRouter(prefix="/resume", tags=["resume"])


class ResumeUploadResponse(BaseModel):
    id: UUID
    file_name: str
    file_type: str
    created_at: datetime


@router.post("/upload", response_model=ResumeUploadResponse, status_code=status.HTTP_201_CREATED)
def upload_resume(
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ResumeUploadResponse:
    if settings.environment != "development":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Resume upload is available only in local development",
        )
    try:
        document = save_resume_document(file, db, settings.upload_dir)
    except ResumeUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return ResumeUploadResponse(
        id=document.id,
        file_name=document.file_name,
        file_type=document.file_type,
        created_at=document.created_at,
    )
