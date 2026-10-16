"""Validated application settings loaded from environment or a local .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from sqlalchemy.engine import make_url
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = BACKEND_ROOT / "data" / "offerpilot.db"
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"
DEFAULT_UPLOAD_DIR = BACKEND_ROOT / "uploads"


class Settings(BaseSettings):
    app_name: str = "OfferPilot API"
    environment: str = "development"
    database_url: str = Field(default=DEFAULT_DATABASE_URL, repr=False)
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=5, ge=0, le=50)
    database_pool_timeout_seconds: int = Field(default=5, ge=1, le=60)
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    database_statement_timeout_ms: int = Field(default=15000, ge=100, le=120000)
    database_lock_timeout_ms: int = Field(default=5000, ge=100, le=30000)
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
    deepseek_extraction_enabled: bool = False
    byok_ttl_seconds: int = Field(default=3600, ge=60, le=3600)

    @property
    def deepseek_extraction_available(self) -> bool:
        # Supplier BYOK permission / retention / training configuration is a
        # release gate. This batch enables only explicit local development.
        return self.environment == "development" and self.deepseek_extraction_enabled
    extraction_timeout_seconds: int = Field(default=30, ge=1, le=60)
    extraction_max_attempts: int = Field(default=2, ge=1, le=2)
    extraction_max_calls: int = Field(default=4, ge=1, le=4)
    extraction_max_active_per_user: int = Field(default=3, ge=1, le=10)
    extraction_max_active_total: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_database(self) -> "Settings":
        try:
            url = make_url(self.database_url)
        except Exception:
            raise ValueError("数据库配置无效") from None
        if url.drivername == "postgresql":
            url = url.set(drivername="postgresql+psycopg")
            self.database_url = url.render_as_string(hide_password=False)
        if url.drivername not in {"sqlite", "postgresql+psycopg"}:
            raise ValueError("仅支持 SQLite 或 PostgreSQL psycopg 驱动")
        if self.environment != "development":
            if url.drivername != "postgresql+psycopg":
                raise ValueError("非开发环境必须显式配置 PostgreSQL")
            if not all((url.host, url.database, url.username, url.password)):
                raise ValueError("非开发环境数据库配置不完整")
            if url.username == "postgres":
                raise ValueError("应用不得使用数据库超级用户")
        return self

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
        hide_input_in_errors=True,
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
