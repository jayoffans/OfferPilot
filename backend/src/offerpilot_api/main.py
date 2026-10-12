"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from offerpilot_api.api.routes.auth import router as auth_router
from offerpilot_api.api.routes.health import router as health_router
from offerpilot_api.api.routes.resume import router as resume_router
from offerpilot_api.api.routes.profile import router as profile_router
from offerpilot_api.core.config import get_settings
from offerpilot_api.identity.services import IdentityError
from offerpilot_api.db.session import engine
from offerpilot_api.resume_ingestion.lifecycle import ResumeLifecycleError
from offerpilot_api.profile.services import ProfileError


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        yield
    finally:
        # Close pooled SQLite handles on graceful shutdown, especially Windows.
        engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.include_router(auth_router)
    app.include_router(health_router)
    app.include_router(resume_router)
    app.include_router(profile_router)

    @app.exception_handler(ProfileError)
    async def profile_error(_request: Request, exc: ProfileError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code, "message": exc.message}},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(ResumeLifecycleError)
    async def lifecycle_error(_request: Request, exc: ResumeLifecycleError) -> JSONResponse:
        headers = {"Cache-Control": "no-store"}
        if exc.resume_id:
            headers["Location"] = f"/api/v1/resumes/{exc.resume_id}/lifecycle"
        return JSONResponse(
            status_code=exc.status_code, content={"detail": {"code": exc.code, "message": exc.message}},
            headers=headers,
        )

    @app.exception_handler(IdentityError)
    async def identity_error(_request: Request, exc: IdentityError) -> JSONResponse:
        headers = {"Cache-Control": "no-store"}
        if exc.status_code == 429:
            headers["Retry-After"] = str(exc.retry_after_seconds or 900)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code, "message": exc.message}},
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default validation body can echo passwords, invitation
        # tokens, and arbitrary user inputs. Never include input or ctx here.
        known_fields = {
            "email", "password", "invite_token", "file", "file_name", "user_id", "resume_id",
            "revision", "education", "skills", "projects", "experiences", "school", "degree", "major",
            "start_date", "end_date", "end_date_kind", "name", "level", "role", "description", "is_current",
            "organization", "experience_type", "action", "value", "id", "source",
        }
        fields = sorted({str(e["loc"][-1]) for e in exc.errors() if e.get("loc") and e["loc"][-1] in known_fields})
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": "invalid_request", "message": "请求字段无效", "fields": fields}},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request: Request, _exc: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "storage_unavailable", "message": "服务暂时不可用，请稍后重试"}},
            headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def private_api_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/") or request.url.path.startswith("/resume/"):
            response.headers["Cache-Control"] = "no-store"
        return response
    return app


app = create_app()
