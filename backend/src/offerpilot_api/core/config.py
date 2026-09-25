"""Validated application settings loaded from environment or a local .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = BACKEND_ROOT / "data" / "offerpilot.db"
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"
DEFAULT_UPLOAD_DIR = BACKEND_ROOT / "uploads"


class Settings(BaseSettings):
    app_name: str = "OfferPilot API"
    environment: str = "development"
    database_url: str = DEFAULT_DATABASE_URL
    upload_dir: Path = DEFAULT_UPLOAD_DIR

    model_config = SettingsConfigDict(
        env_prefix="OFFERPILOT_",
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
