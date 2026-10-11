"""Invitation registration and cookie-backed server-session APIs."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from offerpilot_api.core.api_errors import error_responses
from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.models import AuthSession, User
from offerpilot_api.db.session import get_db
from offerpilot_api.identity.dependencies import check_origin, get_current_session, get_current_user
from offerpilot_api.identity.schemas import CsrfResponse, LoginRequest, LoginResponse, RegisterRequest, UserResponse
from offerpilot_api.identity.security import SESSION_COOKIE_NAME, csrf_token
from offerpilot_api.identity.services import IdentityError, login_user, register_user, revoke_session

router = APIRouter(
    prefix="/api/v1", tags=["identity"],
    responses=error_responses(401, 403, 422, 429, 503),
)


def _json_request(request: Request, settings: Settings) -> None:
    check_origin(request, settings)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise IdentityError("invalid_content_type", "请使用 JSON 请求", 415)


@router.post(
    "/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 409, 415),
)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserResponse:
    _json_request(request, settings)
    user = register_user(
        db,
        email=str(payload.email),
        password=payload.password.get_secret_value(),
        invite_token=payload.invite_token.get_secret_value(),
        client_host=request.client.host if request.client else "unknown",
        settings=settings,
    )
    response.headers["Cache-Control"] = "no-store"
    return UserResponse.model_validate(user)


@router.post("/auth/login", response_model=LoginResponse, responses=error_responses(415))
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    _json_request(request, settings)
    user, token = login_user(
        db,
        email=str(payload.email),
        password=payload.password.get_secret_value(),
        client_host=request.client.host if request.client else "unknown",
        settings=settings,
        previous_token=request.cookies.get(SESSION_COOKIE_NAME),
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=settings.session_ttl_seconds,
        path="/",
        secure=settings.secure_session_cookie,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return LoginResponse(user=UserResponse.model_validate(user), csrf_token=csrf_token(token))


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    session: Annotated[AuthSession, Depends(get_current_session)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    revoke_session(db, session)
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=settings.secure_session_cookie,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/me", response_model=UserResponse)
def me(user: Annotated[User, Depends(get_current_user)], response: Response) -> UserResponse:
    response.headers["Cache-Control"] = "no-store"
    return UserResponse.model_validate(user)


@router.get("/auth/csrf", response_model=CsrfResponse)
def csrf(
    request: Request,
    response: Response,
    session: Annotated[AuthSession, Depends(get_current_session)],
) -> CsrfResponse:
    response.headers["Cache-Control"] = "no-store"
    return CsrfResponse(csrf_token=csrf_token(request.cookies[SESSION_COOKIE_NAME]))
