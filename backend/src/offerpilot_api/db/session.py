"""SQLite engine and request-scoped SQLAlchemy sessions."""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from offerpilot_api.core.config import BACKEND_ROOT, get_settings


def _prepare_database_url(database_url: str):
    """Create a local SQLite parent directory and anchor relative paths to backend/."""

    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return url

    database_path = Path(url.database)
    if not database_path.is_absolute():
        database_path = (BACKEND_ROOT / database_path).resolve()

    database_path.parent.mkdir(parents=True, exist_ok=True)
    return url.set(database=str(database_path))


def _build_engine() -> Engine:
    settings = get_settings()
    url = _prepare_database_url(settings.database_url)
    connect_args = {"check_same_thread": False} if url.get_backend_name() == "sqlite" else {}

    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """Yield one SQLAlchemy session per request and always close it."""

    with SessionLocal() as session:
        yield session
