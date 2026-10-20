"""Resolve identity only from a valid server session, with CSRF on mutations."""

import hmac
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.models import AuthSession, User
from offerpilot_api.db.session import get_db
from offerpilot_api.identity.security import SESSION_COOKIE_NAME, csrf_token
from offerpilot_api.identity.services import IdentityError, authenticated_session


def check_origin(request: Request, settings: Settings) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.trusted_origins:
        raise IdentityError("untrusted_origin", "请求来源未获允许", 403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise IdentityError("untrusted_origin", "请求来源未获允许", 403)


def get_current_session(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthSession:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    db.info["account_settings"] = settings
    session = authenticated_session(db, token)
    db.info["account_user_id"] = session.user_id
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request, settings)
        supplied = request.headers.get("x-csrf-token", "")
        valid_format = len(supplied) == 64 and all(char in "0123456789abcdef" for char in supplied)
        if not token or not valid_format or not hmac.compare_digest(supplied, csrf_token(token)):
            raise IdentityError("csrf_failed", "请求验证失败，请刷新后重试", 403)
    return session


def get_current_user(
    session: Annotated[AuthSession, Depends(get_current_session)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise IdentityError("authentication_required", "会话已失效，请重新登录", 401)
    return user
