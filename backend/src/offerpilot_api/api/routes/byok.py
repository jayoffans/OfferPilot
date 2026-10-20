"""Session credential and separate no-cost disclosure preview endpoints."""
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from offerpilot_api.agent_runtime.byok import KeyRequest, KeyStatus, ExtractionPreview, redact_resume, invalidate_session_runs
from offerpilot_api.agent_runtime.extraction_runs import ExtractionRunError, _document, source_version
from offerpilot_api.core.api_errors import error_responses
from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.models import AuthSession, User
from offerpilot_api.db.session import get_db
from offerpilot_api.identity.dependencies import get_current_session, get_current_user

router = APIRouter(prefix="/api/v1", tags=["session-byok"], responses=error_responses(401, 403, 404, 409, 422, 429, 503))
DB = Annotated[Session, Depends(get_db)]
CurrentSession = Annotated[AuthSession, Depends(get_current_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]
Config = Annotated[Settings, Depends(get_settings)]


@router.get("/ai/deepseek-key", response_model=KeyStatus)
def key_status(request: Request, db: DB, session: CurrentSession, user: CurrentUser, settings: Config):
    return request.app.state.byok_vault.status(db, user_id=user.id, session_id=session.id, settings=settings)


@router.put("/ai/deepseek-key", response_model=KeyStatus)
def configure_key(payload: KeyRequest, request: Request, db: DB, session: CurrentSession, user: CurrentUser, settings: Config):
    if not settings.deepseek_extraction_available:
        raise ExtractionRunError("deepseek_disabled", "真实解析未开启，生产供应商与 BYOK 条件待审核", 503)
    vault = request.app.state.byok_vault
    invalidate_session_runs(db, session_id=session.id)
    try:
        vault.put(user=user, session=session, api_key=payload.api_key.get_secret_value(), ttl_seconds=settings.byok_ttl_seconds)
    except ValueError:
        db.rollback()
        raise ExtractionRunError("key_capacity_reached", "当前临时 Key 容量已满，请稍后配置", 429) from None
    db.commit()
    return vault.status(db, user_id=user.id, session_id=session.id, settings=settings)


@router.delete("/ai/deepseek-key", response_model=KeyStatus)
def revoke_key(request: Request, db: DB, session: CurrentSession, user: CurrentUser, settings: Config):
    invalidate_session_runs(db, session_id=session.id)
    request.app.state.byok_vault.revoke(session.id)
    db.commit()
    return request.app.state.byok_vault.status(db, user_id=user.id, session_id=session.id, settings=settings)


@router.get("/resumes/{resume_id}/extraction-preview", response_model=ExtractionPreview)
def preview(resume_id: UUID, request: Request, db: DB, session: CurrentSession, user: CurrentUser, settings: Config):
    document = _document(db, user.id, resume_id)
    if not settings.deepseek_extraction_available:
        raise ExtractionRunError("deepseek_disabled", "真实解析未开启", 503)
    credential = request.app.state.byok_vault.credential(db, user_id=user.id, session_id=session.id)
    if not credential:
        raise ExtractionRunError("key_unavailable", "请先配置当前会话的 DeepSeek Key", 409)
    if len(document.raw_text) > 50000:
        raise ExtractionRunError("resume_text_exceeds_limit", "文本超过 50,000 字符，请提供较短的简历", 422)
    text = redact_resume(document.raw_text)
    if not text.replace("*", "").strip():
        raise ExtractionRunError("resume_sections_unrecognized", "未找到教育、技能、项目或工作经历标题，请调整 PDF 标题或手工填写", 422)
    try:
        return request.app.state.byok_vault.preview(user_id=user.id, session_id=session.id, reference=credential.reference,
            resume_id=resume_id, source_version=source_version(document), text=text)
    except ValueError:
        raise ExtractionRunError("preview_capacity_reached", "授权预览过多，请稍后再试", 429) from None
