"""FastAPI application entry point."""

from fastapi import FastAPI

from offerpilot_api.api.routes.health import router as health_router
from offerpilot_api.api.routes.resume import router as resume_router
from offerpilot_api.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.include_router(health_router)
    app.include_router(resume_router)
    return app


app = create_app()
