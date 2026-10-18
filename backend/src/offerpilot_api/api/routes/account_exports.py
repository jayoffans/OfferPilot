"""Authenticated, reverified account data export endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from offerpilot_api.account_lifecycle.export_schemas import ExportCreateRequest, ExportStatus
from offerpilot_api.account_lifecycle.exports import create_export, download_export, export_status, get_export, list_exports
from offerpilot_api.account_lifecycle.guards import reauthenticate
from offerpilot_api.core.api_errors import error_responses
from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.models import User
from offerpilot_api.db.session import get_db
from offerpilot_api.identity.dependencies import get_current_user

router = APIRouter(prefix="/api/v1/me", tags=["account-data"], responses=error_responses(401, 403, 404, 409, 410, 422, 429, 503))


@router.post("/export", response_model=ExportStatus, status_code=202)
def request_export(payload: ExportCreateRequest, db: Annotated[Session, Depends(get_db)],
                   settings: Annotated[Settings, Depends(get_settings)],
                   user: Annotated[User, Depends(get_current_user)]) -> ExportStatus:
    reauthenticate(db, user, payload.password.get_secret_value(), settings)
    return export_status(create_export(db, user_id=user.id, idempotency_key=payload.idempotency_key, settings=settings))


@router.get("/exports", response_model=list[ExportStatus])
def export_history(db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]):
    return list_exports(db, user_id=user.id)


@router.get("/export/{export_id}", response_model=ExportStatus)
def export_detail(export_id: UUID, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]):
    return export_status(get_export(db, user_id=user.id, export_id=export_id))


@router.get("/export/{export_id}/download", response_class=StreamingResponse,
            responses={200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}})
def download(export_id: UUID, db: Annotated[Session, Depends(get_db)],
             settings: Annotated[Settings, Depends(get_settings)], user: Annotated[User, Depends(get_current_user)]):
    source, size = download_export(db, user_id=user.id, export_id=export_id, settings=settings)
    def chunks():
        try:
            while block := source.read(1024 * 1024):
                yield block
        finally:
            source.close()
    return StreamingResponse(chunks(), media_type="application/zip", background=BackgroundTask(source.close), headers={
        "Content-Disposition": f'attachment; filename="offerpilot-data-{export_id}.zip"',
        "Content-Length": str(size), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })
