"""FastAPI application entry point."""

from contextlib import asynccontextmanager
import asyncio
from contextlib import suppress
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from offerpilot_api.api.routes.auth import router as auth_router
from offerpilot_api.api.routes.health import router as health_router
from offerpilot_api.api.routes.resume import router as resume_router
from offerpilot_api.api.routes.profile import router as profile_router
from offerpilot_api.api.routes.extraction import router as extraction_router
from offerpilot_api.api.routes.byok import router as byok_router
from offerpilot_api.api.routes.jobs import router as jobs_router
from offerpilot_api.api.routes.job_matches import router as job_matches_router
from offerpilot_api.api.routes.applications import router as applications_router
from offerpilot_api.api.routes.dashboard import router as dashboard_router
from offerpilot_api.application_tracker.services import ApplicationError
from offerpilot_api.agent_runtime.byok import SessionKeyVault
from offerpilot_api.agent_runtime.extraction_runs import ExtractionRunError, extraction_dispatcher, mock_provider_factory
from offerpilot_api.core.config import get_settings
from offerpilot_api.identity.services import IdentityError
from offerpilot_api.db.session import SessionLocal, engine, verify_runtime_database_role
from offerpilot_api.resume_ingestion.lifecycle import ResumeLifecycleError
from offerpilot_api.profile.services import ProfileError
from offerpilot_api.job_catalog.services import JobError
from offerpilot_api.job_intelligence.match_services import MatchError


@asynccontextmanager
async def lifespan(_app: FastAPI):
    dispatcher = None
    settings = get_settings()
    if settings.environment != "development":
        await asyncio.to_thread(verify_runtime_database_role, engine)
    if settings.mock_extraction_available or settings.deepseek_extraction_available:
        dispatcher = asyncio.create_task(extraction_dispatcher(_app, SessionLocal))
    try:
        yield
    finally:
        if dispatcher is not None:
            dispatcher.cancel()
            with suppress(asyncio.CancelledError):
                await dispatcher
        _app.state.byok_vault.clear()
        # Close pooled SQLite handles on graceful shutdown, especially Windows.
        engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.deepseek_extraction_available and any(os.environ.get(name, "1") != "1" for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS")):
        raise RuntimeError("Session BYOK requires one API process")
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.include_router(auth_router)
    app.include_router(health_router)
    app.include_router(resume_router)
    app.include_router(profile_router)
    app.include_router(extraction_router)
    app.include_router(byok_router)
    app.include_router(jobs_router)
    app.include_router(job_matches_router)
    app.include_router(applications_router)
    app.include_router(dashboard_router)
    app.state.byok_vault = SessionKeyVault()
    app.state.extraction_settings = settings
    app.state.deepseek_provider_factory = None
    app.state.extraction_provider_factory = mock_provider_factory
    app.state.extraction_dispatcher_enabled = True

    @app.exception_handler(ApplicationError)
    async def application_error(_request: Request, exc: ApplicationError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": {"code": exc.code, "message": exc.message}},
                            headers={"Cache-Control": "no-store"})

    @app.exception_handler(JobError)
    async def job_error(_request: Request, exc: JobError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": {"code": exc.code, "message": exc.message}},
                            headers={"Cache-Control": "no-store"})

    @app.exception_handler(MatchError)
    async def match_error(_request: Request, exc: MatchError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": {"code": exc.code, "message": exc.message}},
                            headers={"Cache-Control": "no-store"})

    @app.exception_handler(ExtractionRunError)
    async def extraction_error(_request: Request, exc: ExtractionRunError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": {"code": exc.code, "message": exc.message}},
                            headers={"Cache-Control": "no-store"})

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
            "idempotency_key", "source_version", "mode", "run_id",
            "title", "company", "location", "salary", "work_type", "source_url", "jd_text",
            "requirements", "requirement", "type", "content", "priority", "review_status", "evidence_text",
            "source_start", "source_jd_version", "is_favorite", "q", "sort", "page", "page_size",
            "profile_revision", "job_revision", "preferences", "locations", "work_types", "target_roles",
            "min", "max", "currency", "period", "tax", "weekly_days", "internship_months", "limit",
            "job_id", "match_id", "reapply", "status", "submitted_at", "notes", "next_action",
            "occurred_at", "due_date", "due_time", "timezone", "confirmed",
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
