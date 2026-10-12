"""Validated application settings loaded from environment or a local .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
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
    session_ttl_seconds: int = Field(default=43200, ge=60, le=604800)
    session_cookie_secure: bool | None = None
    trusted_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]
    auth_attempt_window_seconds: int = Field(default=900, ge=60, le=86400)
    auth_login_attempt_limit: int = Field(default=5, ge=1, le=100)
    auth_ip_attempt_limit: int = Field(default=30, ge=1, le=1000)
    auth_registration_attempt_limit: int = Field(default=10, ge=1, le=100)
    # Test-only, opt-in; never resolves or calls a paid provider.
    mock_extraction_enabled: bool = False
    extraction_timeout_seconds: int = Field(default=30, ge=1, le=60)
    extraction_max_attempts: int = Field(default=2, ge=1, le=2)
    extraction_max_calls: int = Field(default=4, ge=1, le=4)
    extraction_max_active_per_user: int = Field(default=3, ge=1, le=10)
    extraction_max_active_total: int = Field(default=20, ge=1, le=100)

    @property
    def mock_extraction_available(self) -> bool:
        return self.environment == "development" and self.mock_extraction_enabled

    @property
    def secure_session_cookie(self) -> bool:
        if self.environment != "development":
            return True
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return False

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
