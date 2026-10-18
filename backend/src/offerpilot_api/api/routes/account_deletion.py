from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from offerpilot_api.account_lifecycle.deletion import begin_deletion, now
from offerpilot_api.account_lifecycle.schemas import AccountDeletionRequest, AccountDeletionResponse
from offerpilot_api.core.api_errors import error_responses
from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.models import AccountDeletion, User
from offerpilot_api.db.session import get_db
from offerpilot_api.identity.dependencies import get_current_user
from offerpilot_api.identity.security import SESSION_COOKIE_NAME, token_hash, valid_token
from offerpilot_api.identity.services import IdentityError

STATUS_COOKIE = "offerpilot_deletion_status"
router = APIRouter(prefix="/api/v1/me/deletion", tags=["account lifecycle"], responses=error_responses(401,403,422,429,503))


@router.post("", response_model=AccountDeletionResponse, status_code=202)
def create_deletion(payload: AccountDeletionRequest, request: Request, response: Response,
                    user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)],
                    settings: Annotated[Settings, Depends(get_settings)]):
    receipt, status_token = begin_deletion(db, user=user, password=payload.password.get_secret_value(),
        idempotency_key=payload.idempotency_key, session_token=request.cookies[SESSION_COOKIE_NAME],
        settings=settings, vault=request.app.state.byok_vault)
    if status_token:
        response.set_cookie(STATUS_COOKIE, status_token, max_age=3600, path="/", secure=settings.secure_session_cookie,
                            httponly=True, samesite="strict")
    # Retain the revoked HttpOnly session cookie for the same one-hour minimal
    # status fallback when the response containing the status cookie is lost.
    return AccountDeletionResponse.model_validate(receipt)


@router.get("/status", response_model=AccountDeletionResponse, responses=error_responses(404))
def deletion_status(request: Request, db: Annotated[Session, Depends(get_db)]):
    tokens = [(request.cookies.get(STATUS_COOKIE), AccountDeletion.status_token_hash),
              (request.cookies.get(SESSION_COOKIE_NAME), AccountDeletion.status_session_hash)]
    conditions = [column == token_hash(token) for token, column in tokens if token and valid_token(token)]
    if not conditions:
        raise IdentityError("deletion_status_unavailable", "处理状态凭证已失效，请联系维护者核查", 404)
    receipt = db.scalar(select(AccountDeletion).where(AccountDeletion.status_expires_at > now(), or_(*conditions)))
    if not receipt:
        raise IdentityError("deletion_status_unavailable", "处理状态凭证已失效，请联系维护者核查", 404)
    return AccountDeletionResponse.model_validate(receipt)
